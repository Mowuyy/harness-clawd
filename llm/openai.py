"""OpenAI 官方 API 供应商。

适用于:
  - gpt-4o / gpt-4o-mini    — 通用对话模型
  - o1 / o3-mini            — 推理模型（不支持 stream，请使用 stream=False）

环境变量:
  OPENAI_API_KEY   API 密钥（必填）
  OPENAI_MODEL     模型名称（默认 gpt-4o）
"""
from __future__ import annotations

import os
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from .base import LLMProvider
from .types import Messages, StreamOutput, StreamStatus

_DEFAULT_MODEL = "gpt-4o"


class OpenAIProvider(LLMProvider):
    """使用官方 OpenAI API 的供应商。

    - 非流式 invoke 由基类 ``LLMProvider._invoke`` 统一处理。
    - 流式 invoke 覆盖 ``_stream``，迭代 ``delta.content`` 增量并按
      :class:`StreamStatus` yield 事件帧。
    """

    name: str = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model or os.environ.get("OPENAI_MODEL", _DEFAULT_MODEL)
        self._client: AsyncOpenAI | None = None

    @property
    def model(self) -> str:
        return self._model

    def build_client(self) -> AsyncOpenAI:
        """延迟单例：首次调用时初始化客户端，此后复用。"""
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def _stream(
        self,
        messages: Messages,
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        extra_body: dict[str, Any] | None = None,
        **extra: Any,
    ) -> AsyncGenerator[StreamOutput, None]:
        """流式实现，迭代 ``delta.content`` 增量并 yield :class:`StreamOutput`。

        遍历示例::

            async for ev in provider.invoke(msgs, stream=True):
                match ev.status:
                    case StreamStatus.ANSWER_START:
                        print("[answering...]")
                    case StreamStatus.ANSWERING:
                        print(ev.answer, end="", flush=True)
                    case StreamStatus.ANSWER_END:
                        print("[answer done]")
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
            content: str | None = delta.content
            if content:
                if not in_answering:
                    in_answering = True
                    yield StreamOutput(status=StreamStatus.ANSWER_START)
                yield StreamOutput(status=StreamStatus.ANSWERING, answer=content)

        yield StreamOutput(status=StreamStatus.ANSWER_END)

