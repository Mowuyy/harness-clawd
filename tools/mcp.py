"""MCP 客户端工具：连接 MCP 服务器并将其工具包装为原生 Tool。

涵盖: MCPToolWrapper, connect_mcp_servers
支持传输类型: stdio / sse / streamableHttp
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx

from .base import Tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCPToolWrapper
# ---------------------------------------------------------------------------

class MCPToolWrapper(Tool):
    """将单个 MCP 服务器工具包装为 Tool 实例。"""

    def __init__(self, session: Any, server_name: str, tool_def: Any, tool_timeout: int = 30):
        self._session = session
        self._original_name: str = tool_def.name
        self._name = f"mcp_{server_name}_{tool_def.name}"
        self._description: str = tool_def.description or tool_def.name
        self._parameters: dict = tool_def.inputSchema or {"type": "object", "properties": {}}
        self._tool_timeout = tool_timeout

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        try:
            from mcp import types
        except ImportError:
            return "Error: mcp package not installed. Run: pip install mcp"

        try:
            result = await asyncio.wait_for(
                self._session.call_tool(self._original_name, arguments=kwargs),
                timeout=self._tool_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("MCP tool '%s' timed out after %ds", self._name, self._tool_timeout)
            return f"(MCP tool call timed out after {self._tool_timeout}s)"
        except asyncio.CancelledError:
            # MCP SDK 的 anyio cancel scope 可能泄漏 CancelledError，只在外部取消时重新抛出。
            task = asyncio.current_task()
            if task is not None and task.cancelling() > 0:
                raise
            logger.warning("MCP tool '%s' was cancelled by server/SDK", self._name)
            return "(MCP tool call was cancelled)"
        except Exception as exc:
            logger.error("MCP tool '%s' failed: %s: %s", self._name, type(exc).__name__, exc)
            return f"(MCP tool call failed: {type(exc).__name__})"

        try:
            from mcp import types as mcp_types
            parts = []
            for block in result.content:
                if isinstance(block, mcp_types.TextContent):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            return "\n".join(parts) or "(no output)"
        except Exception:
            return str(result)


# ---------------------------------------------------------------------------
# connect_mcp_servers
# ---------------------------------------------------------------------------

async def connect_mcp_servers(
    mcp_servers: dict[str, Any],
    registry: Any,           # ToolRegistry
    stack: AsyncExitStack,
) -> None:
    """连接配置的 MCP 服务器，将其工具注册到 registry。

    mcp_servers 格式::

        {
          "my_server": {
            "type": "stdio" | "sse" | "streamableHttp",  # 可省略，自动推断
            "command": "npx ...",          # stdio 必填
            "args": [...],                 # stdio 可选
            "env": {...},                  # stdio 可选
            "url": "http://...",           # sse / streamableHttp 必填
            "headers": {...},              # sse / streamableHttp 可选
            "enabled_tools": ["*"],        # 要注册的工具名，"*" 表示全部
            "tool_timeout": 30,
          }
        }
    """
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.sse import sse_client
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client
    except ImportError:
        logger.error("mcp package not installed — skipping MCP servers. Run: pip install mcp")
        return

    for name, cfg in mcp_servers.items():
        try:
            # 推断传输类型
            transport_type: str = cfg.get("type", "")
            if not transport_type:
                if cfg.get("command"):
                    transport_type = "stdio"
                elif cfg.get("url", "").rstrip("/").endswith("/sse"):
                    transport_type = "sse"
                elif cfg.get("url"):
                    transport_type = "streamableHttp"
                else:
                    logger.warning("MCP server '%s': no command or url — skipping", name)
                    continue

            if transport_type == "stdio":
                params = StdioServerParameters(
                    command=cfg["command"],
                    args=cfg.get("args", []),
                    env=cfg.get("env") or None,
                )
                read, write = await stack.enter_async_context(stdio_client(params))

            elif transport_type == "sse":
                merged_headers = cfg.get("headers") or {}

                def _httpx_factory(
                    headers: dict | None = None,
                    timeout: Any = None,
                    auth: Any = None,
                    _base: dict = merged_headers,
                ) -> httpx.AsyncClient:
                    return httpx.AsyncClient(
                        headers={**_base, **(headers or {})},
                        follow_redirects=True,
                        timeout=timeout,
                        auth=auth,
                    )

                read, write = await stack.enter_async_context(
                    sse_client(cfg["url"], httpx_client_factory=_httpx_factory)
                )

            elif transport_type == "streamableHttp":
                http_client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=cfg.get("headers") or None,
                        follow_redirects=True,
                        timeout=None,
                    )
                )
                read, write, _ = await stack.enter_async_context(
                    streamable_http_client(cfg["url"], http_client=http_client)
                )

            else:
                logger.warning("MCP server '%s': unknown transport '%s' — skipping", name, transport_type)
                continue

            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            tools_resp = await session.list_tools()
            enabled_tools: set[str] = set(cfg.get("enabled_tools", ["*"]))
            allow_all = "*" in enabled_tools
            registered = 0

            for tool_def in tools_resp.tools:
                wrapped_name = f"mcp_{name}_{tool_def.name}"
                if not allow_all and tool_def.name not in enabled_tools and wrapped_name not in enabled_tools:
                    continue
                wrapper = MCPToolWrapper(session, name, tool_def, tool_timeout=cfg.get("tool_timeout", 30))
                registry.register(wrapper)
                registered += 1

            logger.info("MCP server '%s': connected, %d tools registered", name, registered)

        except Exception as e:
            logger.error("MCP server '%s': failed to connect: %s", name, e)
