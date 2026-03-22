"""自定义 / OpenAI 兼容 API 供应商。

适用于 Anthropic (via OpenAI-compat)、Azure OpenAI、Ollama、
本地 vllm/llama.cpp 等任何遵循 OpenAI Chat Completions 格式的服务。

环境变量:
  CUSTOM_API_KEY    API 密钥（部分服务不需要，留空即可）
  CUSTOM_BASE_URL   服务端 base_url（必填，例如 http://localhost:11434/v1）
  CUSTOM_MODEL      模型名称（必填）
  CUSTOM_SSL_VERIFY 是否验证 SSL 证书（默认 false；设为 true 开启验证）
"""
from __future__ import annotations

import os
from typing import Any, AsyncGenerator, Iterable, TypeVar

import httpx
from openai import AsyncOpenAI
from openai._types import NOT_GIVEN, NotGiven
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_tool_union_param import ChatCompletionToolUnionParam
from pydantic import BaseModel

from .base import LLMProvider
from .types import Messages, ResponseFormat, StreamOutput, StreamStatus

T = TypeVar("T", bound=BaseModel)


class CustomProvider(LLMProvider):
    """OpenAI 兼容接口的自定义供应商。

    invoke 由基类 ``LLMProvider`` 提供，本类仅负责客户端初始化。
    """

    name: str = "custom"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        ssl_verify: bool | None = None,
        max_connections: int = 100,
        keepalive_expiry: float = 1800,
        max_retries: int = 3,
        timeout: float = 1800,
    ) -> None:
        self._base_url = base_url or os.environ.get("CUSTOM_BASE_URL", "")
        self._api_key = api_key or os.environ.get("CUSTOM_API_KEY", "sk-placeholder")
        self._model = model or os.environ.get("CUSTOM_MODEL", "")
        if ssl_verify is None:
            ssl_verify = os.environ.get("CUSTOM_SSL_VERIFY", "false").lower() != "false"
        self._ssl_verify = ssl_verify
        self._max_connections = max_connections
        self._keepalive_expiry = keepalive_expiry
        self._max_retries = max_retries
        self._timeout = timeout
        self._client: AsyncOpenAI | None = None

        if not self._base_url:
            raise ValueError("CustomProvider requires base_url or CUSTOM_BASE_URL env var")
        if not self._model:
            raise ValueError("CustomProvider requires model or CUSTOM_MODEL env var")

    @property
    def model(self) -> str:
        return self._model

    def build_client(self) -> AsyncOpenAI:
        """延迟单例，以连接池配置初始化客户端。"""
        if self._client is None:
            http_client = httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(
                    verify=self._ssl_verify,
                    limits=httpx.Limits(
                        max_connections=self._max_connections,
                        max_keepalive_connections=self._max_connections,
                        keepalive_expiry=self._keepalive_expiry,
                    ),
                )
            )
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                max_retries=self._max_retries,
                timeout=self._timeout,
                http_client=http_client,
            )
        return self._client

    async def _invoke(
        self,
        messages: Messages,
        model: str,
        response_format: type[T] | ResponseFormat | None = None,
        tools: Iterable[ChatCompletionToolUnionParam] | NotGiven = NOT_GIVEN,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        extra_body: dict[str, Any] | None = None,
        **extra: Any,
    ) -> T | ChatCompletionMessage:
        """非流式实现，默认关闭思考模式。

        若调用方未显式传入 ``extra_body``，则注入
        ``{"chat_template_kwargs": {"enable_thinking": False}}`` 以禁用
        支持扩展思考的模型（如 Qwen3）的推理输出，避免非预期的 ``<think>`` 块。
        调用方可通过传入自定义 ``extra_body`` 覆盖此默认行为。
        """
        if extra_body is None:
            extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        return await super()._invoke(
            messages=messages,
            model=model,
            response_format=response_format,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=extra_body,
            **extra,
        )

    async def _stream(
        self,
        messages: Messages,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        extra_body: dict[str, Any] | None = None,
        **extra: Any,
    ) -> AsyncGenerator[StreamOutput, None]:
        """流式实现，使用 ``chat.completions.stream`` 并解析 ``<think>...</think>`` 思考块。

        遍历示例::

            async for ev in provider.invoke(msgs, stream=True,
                                            extra_body={"chat_template_kwargs": {"enable_thinking": True}}):
                if ev.thinking_end is False and ev.thinking is None:
                    print("[thinking...]")       # 思考开始
                elif ev.thinking:
                    print(ev.thinking, end="")  # 思考增量
                elif ev.thinking_end:
                    print("[think done]")        # 思考结束
                elif ev.answer_end is False and ev.answer is None:
                    print("[answering...]")      # 回答开始
                elif ev.answer:
                    print(ev.answer, end="")    # 回答增量
                elif ev.answer_end:
                    print("[answer done]")       # 回答结束
        """
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            **extra,
        }
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if extra_body is not None:
            params["extra_body"] = extra_body

        in_thinking: bool = (
            (extra_body or {}).get("chat_template_kwargs", {}).get("enable_thinking", False)
        )
        in_answering = False

        async with self.build_client().chat.completions.stream(**params) as stream:
            async for event in stream:
                if event.type == "content.delta":
                    content = event.delta
                    if in_thinking:
                        if content == "<think>":
                            yield StreamOutput(status=StreamStatus.THINKING_START)
                        elif content == "</think>":
                            in_thinking = False
                            yield StreamOutput(
                                status=StreamStatus.THINKING_END,
                                thinking_snapshot=self._thinking_pattern.sub("", event.snapshot),
                            )
                        else:
                            yield StreamOutput(status=StreamStatus.THINKING, thinking=content)
                    else:
                        if not in_answering:
                            yield StreamOutput(status=StreamStatus.ANSWER_START)
                            in_answering = True
                        yield StreamOutput(status=StreamStatus.ANSWERING, answer=content)

                elif event.type == "content.done":
                    yield StreamOutput(
                        status=StreamStatus.ANSWER_END,
                        answer_snapshot=self._content_pattern.sub("", event.content),
                    )
