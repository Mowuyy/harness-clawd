"""DeepSeek API 供应商。

适用于:
  - deepseek-chat          — 通用对话模型，普通流式输出
  - deepseek-reasoner      — R1 推理模型，delta 含 reasoning_content 思考字段

环境变量:
  DEEPSEEK_API_KEY   API 密钥（必填）
  DEEPSEEK_MODEL     模型名称（默认 deepseek-chat）
"""
from __future__ import annotations

import os
from typing import Any, AsyncGenerator

from openai import AsyncOpenAI

from .base import LLMProvider
from .types import Messages, StreamOutput, StreamStatus

_BASE_URL = "https://api.deepseek.com/v1"
_DEFAULT_MODEL = "deepseek-chat"


class DeepSeekProvider(LLMProvider):
    """DeepSeek 官方 API 供应商。

    - 非流式 invoke 由基类 ``LLMProvider._invoke`` 统一处理。
    - 流式 invoke 覆盖 ``_stream``，兼容 deepseek-chat（普通增量）和
      deepseek-reasoner（``delta.reasoning_content`` 思考字段）两种模式。
    """

    name: str = "deepseek"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self._model = model or os.environ.get("DEEPSEEK_MODEL", _DEFAULT_MODEL)
        self._client: AsyncOpenAI | None = None

    @property
    def model(self) -> str:
        return self._model

    def build_client(self) -> AsyncOpenAI:
        """延迟单例：首次调用时初始化客户端，此后复用。"""
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=_BASE_URL,
            )
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
        """流式实现，自动识别 reasoner 思考字段。

        - ``deepseek-chat``：``delta.content`` 直接作为回答增量。
        - ``deepseek-reasoner``：``delta.reasoning_content`` 为思考增量，
          ``delta.content`` 为回答增量，两者交替出现，本方法按
          :class:`StreamStatus` 正确区分并 yield。

        遍历示例::

            async for ev in provider.invoke(msgs, stream=True):
                match ev.status:
                    case StreamStatus.THINKING_START:
                        print("[thinking...]")
                    case StreamStatus.THINKING:
                        print(ev.thinking, end="", flush=True)
                    case StreamStatus.THINKING_END:
                        print("[think done]")
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

        in_thinking = False
        in_answering = False
        thinking_parts: list[str] = []

        async for chunk in await self.build_client().chat.completions.create(**params):
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            # ---- 思考内容（仅 deepseek-reasoner 有此字段）----
            reasoning: str | None = getattr(delta, "reasoning_content", None)
            if reasoning:
                if not in_thinking:
                    in_thinking = True
                    yield StreamOutput(status=StreamStatus.THINKING_START)
                thinking_parts.append(reasoning)
                yield StreamOutput(status=StreamStatus.THINKING, thinking=reasoning)

            # ---- 回答内容 ----
            content: str | None = delta.content
            if content:
                if in_thinking:
                    # 第一次出现 content 时，思考阶段结束
                    in_thinking = False
                    yield StreamOutput(
                        status=StreamStatus.THINKING_END,
                        thinking_snapshot="".join(thinking_parts),
                    )
                if not in_answering:
                    in_answering = True
                    yield StreamOutput(status=StreamStatus.ANSWER_START)
                yield StreamOutput(status=StreamStatus.ANSWERING, answer=content)

        # 流结束
        if in_thinking:
            # 极端情况：只有思考没有回答（罕见）
            yield StreamOutput(
                status=StreamStatus.THINKING_END,
                thinking_snapshot="".join(thinking_parts),
            )
        yield StreamOutput(status=StreamStatus.ANSWER_END)
