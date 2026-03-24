#!/usr/bin/env python3
"""loop.py — Harness Agent。

架构要点:
  - 所有状态封装在 AgentLoop 实例中，无模块级全局变量。
  - asyncio.Lock 序列化并发的 process() 调用。
  - max_iterations 限制工具调用循环上限。
  - _strip_think() 过滤模型嵌入的 <think>…</think> 思考块。
  - _tool_hint() 生成简洁的工具调用日志。
  - _run_agent_loop() 是内层引擎；process() 是外部 API。
  - _run_subagent() 隔离子代理执行。

REPL 命令:  /compact  /tasks  /team  /inbox
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from dotenv import load_dotenv

from config import Config, config as default_config
from context import (
    Compactor,
    ContextBuilder,
    Message,
)
from llm import LLMProvider
from tools import (
    BackgroundManager,
    FILESYSTEM_TOOLS,
    MessageBus,
    ProtocolManager,
    SkillLoader,
    TaskManager,
    TeammateManager,
    TodoManager,
    ToolRegistry,
    build_registry,
    cleanup_empty_session,
    init_filesystem,
)

load_dotenv(override=True)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
class AgentLoop:
    """Harness 主代理循环。

    所有可变状态均属于实例，模块层仅包含 _main() 入口。
    """

    # Construction
    def __init__(self, cfg: Config | None = None, *, llm: LLMProvider | None = None) -> None:
        self.cfg = cfg or default_config
        self.workdir = self.cfg.workdir
        self.model = self.cfg.llm.model
        self.llm = llm or self.cfg.llm.build_provider()

        init_filesystem(self.workdir)

        # Convenience aliases from config
        self._team_dir = self.cfg.team_dir
        self._inbox_dir = self.cfg.inbox_dir
        self._tasks_dir = self.cfg.shared_tasks_dir
        self._skills_dir = self.cfg.skills_dir
        self._transcript_dir = self.cfg.transcript_dir

        self.todo = TodoManager()
        self.skills = SkillLoader(self._skills_dir)
        self.task_mgr = TaskManager(self._tasks_dir)
        self.bg = BackgroundManager(self.workdir)
        self.bus = MessageBus(self._inbox_dir)
        self.protocol = ProtocolManager(self.bus)
        self.team = TeammateManager(
            self.bus, self.task_mgr,
            self._team_dir, self._tasks_dir,
            self.llm, self.cfg.poll_interval, self.cfg.idle_timeout,
        )

        self.compactor = Compactor(self.llm, self._transcript_dir)
        self.msg = Message()
        self._ctx = ContextBuilder(self.workdir, self.skills)

        self.registry: ToolRegistry = build_registry(
            self.todo, self.task_mgr, self.bg,
            self.bus, self.protocol, self.team, self.skills,
            web_search_cfg=self.cfg.web_search,
        )
        self._tools: list = self.registry.schemas()

        # 序列化并发的 process() 调用
        self._processing_lock = asyncio.Lock()

    # Static helpers
    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> reasoning blocks some models embed."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_name: str, inp: dict[str, Any]) -> str:
        """Format a tool call as a concise log hint: bash("ls -la")."""
        val = next(iter(inp.values()), None) if inp else None
        if not isinstance(val, str):
            return tool_name
        snippet = (val[:60] + "…") if len(val) > 60 else val
        return f'{tool_name}("{snippet}")'

    # Sub-agent
    async def _run_subagent(self, prompt: str, agent_type: str = "Explore") -> str:
        """启动隔离子代理并返回文本摘要。

        工具模式和调度均来自 FILESYSTEM_TOOLS，避免重复定义。
        Explore 类型仅开放只读工具（bash + workspace_file[read]）；其它类型允许完整文件动作。
        """
        # Build lookup from the shared filesystem Tool instances.
        _fs = {t.name: t for t in FILESYSTEM_TOOLS}
        read_only = {"bash", "workspace_file"}
        allowed = read_only if agent_type == "Explore" else set(_fs)
        sub_schemas = [t.to_schema() for t in FILESYSTEM_TOOLS if t.name in allowed]

        msgs: list[dict] = [self.msg.user(prompt)]
        last_content: str | None = None

        for _ in range(self.cfg.sub_max_iter):
            msg = await self.llm.invoke(
                messages=msgs,
                tools=sub_schemas,
                max_tokens=self.cfg.max_tokens,
            )
            msgs.append(self.msg.from_api(msg))
            if not msg.tool_calls:
                last_content = self._strip_think(msg.content) or msg.content
                break
            for tc in msg.tool_calls or []:
                tool = _fs.get(tc.function.name)
                if tool is None:
                    out = f"Unknown tool: {tc.function.name}"
                else:
                    try:
                        args = tc.function.arguments
                        inp = args if isinstance(args, dict) else json.loads(args)
                        if (
                            agent_type == "Explore"
                            and tc.function.name == "workspace_file"
                            and inp.get("action") != "read"
                        ):
                            out = "Error: Explore subagent can only use workspace_file action=read"
                            msgs.append(self.msg.tool(tc.id, out))
                            continue
                        params = tool.cast_params(inp)
                        out = str(await tool.execute(**params))[:self.cfg.tool_result_max_chars]
                    except Exception as exc:
                        out = f"Error: {exc}"
                msgs.append(self.msg.tool(tc.id, out))

        return last_content or "(no summary)"

    # Pre-call helpers
    def _drain_background(self, messages: list) -> None:
        """将已完成的后台任务结果注入上下文。"""
        notifs = self.bg.drain()
        if not notifs:
            return
        txt = "\n".join(
            f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs
        )
        messages.append(self.msg.user(f"<background-results>\n{txt}\n</background-results>"))
        messages.append(self.msg.ack("Noted background results."))

    def _check_inbox(self, messages: list) -> None:
        """将新收到的 lead-inbox 消息注入上下文。"""
        inbox = self.bus.read_inbox("lead")
        if not inbox:
            return
        messages.append(self.msg.user(
            f"<inbox>{json.dumps(inbox, indent=2, ensure_ascii=False)}</inbox>"
        ))
        messages.append(self.msg.ack("Noted inbox messages."))

    # Inner agent loop
    async def _run_agent_loop(self, messages: list) -> tuple[str | None, list]:
        """工具调用迭代引擎，返回 (final_content, messages)。

        - max_iterations 上限，达到后返回友好提示。
        - 过大的工具结果按 tool_result_max_chars 截断。
        - 所有内容字段均过滤 <think> 块。
        """
        rounds_without_todo = 0
        iteration = 0
        final_content: str | None = None

        while iteration < self.cfg.max_iterations:
            iteration += 1

            # s06: compression pipeline
            self.compactor.microcompact(messages)
            if self.compactor.estimate_tokens(messages) > self.cfg.token_threshold:
                logger.info("auto-compact triggered (iter %d)", iteration)
                print("[auto-compact triggered]")
                messages = await self.compactor.auto_compact(messages)

            # s08 + s09/s10
            self._drain_background(messages)
            self._check_inbox(messages)

            # LLM call — system prompt assembled fresh every turn
            full_msgs = [self.msg.system(self._ctx.build_system_prompt())] + messages
            try:
                resp_msg = await self.llm.invoke(
                    messages=full_msgs,
                    tools=self._tools,
                    max_tokens=self.cfg.max_tokens,
                )
            except Exception as exc:
                logger.exception("LLM call failed: %s", exc)
                final_content = f"Error calling LLM: {exc}"
                break

            clean = self._strip_think(resp_msg.content)

            # no tool calls — round ends
            if not resp_msg.tool_calls:
                messages.append(self.msg.from_api(resp_msg))
                final_content = clean
                break

            messages.append(self.msg.from_api(resp_msg))

            used_todo = False
            manual_compress = False

            for tc in resp_msg.tool_calls or []:
                tool_name = tc.function.name
                try:
                    args = tc.function.arguments
                    inp: dict[str, Any] = args if isinstance(args, dict) else json.loads(args)
                except Exception:
                    inp = {}

                hint = self._tool_hint(tool_name, inp)

                try:
                    if tool_name == "compress":
                        manual_compress = True
                        output: str = "Compressing..."
                    elif tool_name == "task":
                        output = await self._run_subagent(
                            inp.get("prompt", ""),
                            inp.get("agent_type", "Explore"),
                        )
                    else:
                        # async dispatch: cast + validate + execute
                        output = await self.registry.execute(tool_name, **inp)
                except Exception as exc:
                    output = f"Error executing {tool_name}: {exc}"
                    logger.error("Tool %s raised: %s", tool_name, exc)

                # truncate oversized results
                output_str = str(output)
                if len(output_str) > self.cfg.tool_result_max_chars:
                    output_str = (
                        output_str[:self.cfg.tool_result_max_chars] + "\n... (truncated)"
                    )

                logger.debug("tool %s → %s", hint, output_str[:120])
                print(f"> {hint}: {output_str[:200]}")
                messages.append(self.msg.tool(tc.id, output_str))

                if tool_name == "TodoWrite":
                    used_todo = True

            # s03: nag if todos stall
            rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
            if self.todo.has_open_items() and rounds_without_todo >= 3:
                messages.append(self.msg.user("<reminder>Update your todos.</reminder>"))

            # s06: manual compress
            if manual_compress:
                logger.info("manual compact triggered")
                print("[manual compact]")
                messages = await self.compactor.auto_compact(messages)

        if final_content is None and iteration >= self.cfg.max_iterations:
            logger.warning("max_iterations (%d) reached", self.cfg.max_iterations)
            final_content = (
                f"I reached the maximum iterations ({self.cfg.max_iterations}) "
                "without completing the task.  Try breaking it into smaller steps."
            )

        # 无论正常结束还是达到上限，均返回结果
        # 若本次对话未产生任何文件，清除空的 session 目录
        cleanup_empty_session()
        return final_content, messages

    # Public API
    async def process(self, messages: list) -> None:
        """处理单个用户轮次，原地修改 messages。

        每次调用为本次对话创建隔离的 session 目录（workdir/.sessions/{uuid}），
        所有 write 操作均落到该目录。通过 asyncio.Lock 序列化并发调用。
        """
        async with self._processing_lock:
            self.cfg.init_session()
            final_content, updated = await self._run_agent_loop(messages)
            messages[:] = updated
            if final_content:
                print(final_content)

    # REPL
    async def repl(self) -> None:
        """Interactive REPL.  Commands: /compact  /tasks  /team  /inbox."""
        history: list = []
        while True:
            try:
                query = input("\033[36mHarnessClawd >> \033[0m")
            except (EOFError, KeyboardInterrupt):
                break
            q = query.strip()
            if q.lower() in ("q", "exit", ""):
                break
            if q == "/compact":
                if history:
                    print("[manual compact via /compact]")
                    history[:] = await self.compactor.auto_compact(history)
                continue
            if q == "/tasks":
                print(self.task_mgr.list_all())
                continue
            if q == "/team":
                print(self.team.list_all())
                continue
            if q == "/inbox":
                print(json.dumps(
                    self.bus.read_inbox("lead"), indent=2, ensure_ascii=False
                ))
                continue
            history.append(self.msg.user(query))
            await self.process(history)
            print()


if __name__ == "__main__":
    # logging.basicConfig(
    #     level=logging.INFO,
    #     format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    #     datefmt="%H:%M:%S",
    # )
    async def _main() -> None:
        from config import Config
        agent = AgentLoop(Config(user_id="test_user"))
        await agent.repl()

    asyncio.run(_main())
