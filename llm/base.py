"""LLM 供应商抽象基类。

核心对话逻辑（invoke / _invoke / _stream）封装在此，子类只需实现
``model`` 属性和 ``build_client()`` 方法。
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Iterable, Optional, TypeVar, overload

import json_repair
from openai import AsyncOpenAI
from openai._types import NOT_GIVEN, NotGiven
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.chat.chat_completion_tool_union_param import ChatCompletionToolUnionParam
from pydantic import BaseModel

from .types import Messages, ResponseFormat, ResponseResult, StreamOutput, StreamStatus

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """所有 LLM 供应商的抽象基类。

    子类只需实现:
      - ``model``        —— 返回模型名称
      - ``build_client`` —— 返回已配置的 AsyncOpenAI 实例（建议内部做延迟单例缓存）

    此基类提供完整的对话实现，包括:
      - 流式 / 非流式调用自动分发
      - Pydantic 结构化输出（``response_format=MyModel``）
      - 思考块解析（``<think>...</think>``）
      - tool_call 参数自动容错修复（json_repair）
    """

    # ----------------------------------------------- 思考块正则
    _thinking_pattern = re.compile(r"</?think>", flags=re.DOTALL)
    _content_pattern  = re.compile(r"(<think>)?.*?</think>", flags=re.DOTALL)

    # ----------------------------------------------- 抽象接口
    @property
    @abstractmethod
    def model(self) -> str:
        """供应商默认模型名称。可通过 invoke(model=...) 覆盖。"""

    @abstractmethod
    def build_client(self) -> AsyncOpenAI:
        """构建并返回 ``AsyncOpenAI`` 客户端。"""

    # ----------------------------------------------- 日志
    @staticmethod
    def _record_log(
        messages: Messages,
        tools: Iterable[ChatCompletionToolUnionParam] | NotGiven = NOT_GIVEN,
    ) -> None:
        items = list(messages)
        if tools and tools is not NOT_GIVEN:
            items.append({"role": "tool", "content": str(tools)})
        body = "\n".join(f"[{m['role']}]: {m.get('content', '')}" for m in items)
        logger.debug("LLM Messages:\n%s\n", body)

    # ----------------------------------------------- 公共入口
    @overload
    def invoke(
        self,
        messages: Messages,
        *,
        model: str | None = ...,
        response_format: type[T],
        tools: Iterable[ChatCompletionToolUnionParam] | NotGiven = ...,
        max_tokens: int | None = ...,
        temperature: float = ...,
        extra_body: dict[str, Any] | None = ...,
        stream: bool = ...,
        **extra: Any,
    ) -> "Coroutine[T]": ...

    @overload
    def invoke(
        self,
        messages: Messages,
        *,
        model: str | None = ...,
        response_format: ResponseFormat | None = ...,
        tools: Iterable[ChatCompletionToolUnionParam] | NotGiven = ...,
        max_tokens: int | None = ...,
        temperature: float = ...,
        extra_body: dict[str, Any] | None = ...,
        stream: bool = ...,
        **extra: Any,
    ) -> "Coroutine[ChatCompletionMessage] | AsyncGenerator[StreamOutput, None]": ...

    def invoke(
        self,
        messages: Messages,
        *,
        model: str | None = None,
        response_format: type[T] | ResponseFormat | None = None,
        tools: Iterable[ChatCompletionToolUnionParam] | NotGiven = NOT_GIVEN,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        extra_body: dict[str, Any] | None = None,
        stream: bool = False,
        **extra: Any,
    ) -> "Coroutine[T | ChatCompletionMessage] | AsyncGenerator[StreamOutput, None]":
        """对话入口，根据 ``stream`` 自动分发到 ``_invoke`` 或 ``_stream``。

        Parameters
        ----------
        messages:
            对话历史。content 支持纯文本或多模态列表（图片/音频/文本）。
        model:
            覆盖默认模型；``None`` 时使用 ``self.model``。
        response_format:
            传入 Pydantic ``BaseModel`` 子类 → 自动调用 ``parse()``，返回解析实例。
            传入 dict（如 ``{"type": "json_object"}``）→ 原样透传给 API。
        tools:
            OpenAI function-calling 工具列表。
        max_tokens:
            最大输出 token 数。
        temperature:
            采样温度，默认 0.7。
        extra_body:
            HTTP 请求体额外字段，例如
            ``{"chat_template_kwargs": {"enable_thinking": True}}``。
        stream:
            ``True`` → ``AsyncGenerator[StreamOutput]``（含思考块信号）。
            ``False`` → ``ChatCompletionMessage``（或 Pydantic 实例）。
        **extra:
            透传给底层 API 的其他参数（``top_p``、``seed``等）。
        """
        resolved = model or self.model
        self._record_log(messages, tools=tools)
        if stream:
            return self._stream(
                messages=messages,
                model=resolved,
                max_tokens=max_tokens,
                temperature=temperature,
                extra_body=extra_body,
                **extra,
            )
        return self._invoke(
            messages=messages,
            model=resolved,
            response_format=response_format,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=extra_body,
            **extra,
        )

    # ----------------------------------------------- 非流式实现
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
        """非流式实现。

        - ``response_format`` 为 Pydantic 类时自动调用 ``parse()``。
        - tool_call.function.arguments 经 json_repair 容错解析。
        """
        use_parse = (
            response_format is not None
            and isinstance(response_format, type)
            and issubclass(response_format, BaseModel)
        )
        func = (
            self.build_client().chat.completions.parse
            if use_parse
            else self.build_client().chat.completions.create
        )
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "tools": tools,
            **extra,
        }
        if response_format is not None:
            params["response_format"] = response_format
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if extra_body is not None:
            params["extra_body"] = extra_body

        response = await func(**params)
        message = response.choices[0].message
        if use_parse:
            return message.parsed  # type: ignore[return-value]
        if message.tool_calls:
            for tc in message.tool_calls:
                tc.function.arguments = json_repair.loads(tc.function.arguments)
        return message

    # ----------------------------------------------- 流式实现
    async def _stream(
        self,
        messages: Messages,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        extra_body: dict[str, Any] | None = None,
        **extra: Any,
    ) -> AsyncGenerator[StreamOutput, None]:
        """通用流式实现，直接转发 API 返回的增量内容。

        使用标准 ``create(stream=True)`` 迭代 chunk，不处理思考块。
        如需 ``<think>`` 解析，请在子类中覆盖此方法（参见 CustomProvider）。
        """
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            **extra,
        }
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if extra_body is not None:
            params["extra_body"] = extra_body

        in_answering = False
        async for chunk in await self.build_client().chat.completions.create(**params):
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            content = delta.content
            if content:
                if not in_answering:
                    yield StreamOutput(status=StreamStatus.ANSWER_START)
                    in_answering = True
                yield StreamOutput(status=StreamStatus.ANSWERING, answer=content)

        yield StreamOutput(status=StreamStatus.ANSWER_END)

    # ----------------------------------------------- 工具方法
    def split_think(self, content: str) -> ResponseResult:
        """将模型输出拆分为思考部分和回答部分。"""
        answer = self._content_pattern.sub("", content)
        thinking = self._thinking_pattern.sub("", content.replace(answer, ""))
        return ResponseResult(thinking=thinking, answer=answer)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model!r})"
