"""LLM 供应商共享类型定义。"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, NamedTuple, Optional, TypeAlias

from openai import AsyncStream
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from pydantic import BaseModel

# --------------------------------------------------------------------------- 消息
ContentPart: TypeAlias = str | dict[str, Any]
"""
单条内容片段，与 OpenAI API 原生格式一致:
  - 纯文本字符串 (str)
  - {"type": "text", "text": "..."}
  - {"type": "image_url", "image_url": {"url": "...", "detail": "auto|high|low"}}
  - {"type": "input_audio", "input_audio": {"data": "...", "format": "wav|mp3"}}
"""

Messages: TypeAlias = list[dict[str, Any]]
"""
对话消息列表，每个元素形如:
  {"role": "user"|"assistant"|"system"|"tool", "content": ContentPart | list[ContentPart]}
tool 消息还需附带 tool_call_id 字段；assistant 消息可含 tool_calls 列表。
"""

# --------------------------------------------------------------------------- 工具
ToolParam: TypeAlias = dict[str, Any]
"""
OpenAI function-calling 工具描述，形如:
  {
    "type": "function",
    "function": {
      "name": "get_weather",
      "description": "...",
      "parameters": {"type": "object", "properties": {...}, "required": [...]}
    }
  }
"""

# --------------------------------------------------------------------------- 响应格式
ResponseFormat: TypeAlias = dict[str, Any]
"""
响应格式约束，常见取值:
  {"type": "text"}
  {"type": "json_object"}
  {"type": "json_schema", "json_schema": {"name": "...", "schema": {...}, "strict": True}}
"""

# --------------------------------------------------------------------------- 原始调用结果
InvokeResult: TypeAlias = ChatCompletion | AsyncStream[ChatCompletionChunk]
"""
build_client().chat.completions.create() 原始返回值类型。
invoke() 层已封装成更高层的 ChatCompletionMessage / AsyncGenerator[StreamOutput]。
"""


# --------------------------------------------------------------------------- 流式输出

class StreamStatus(StrEnum):
    """流式对话中每个事件帧的状态。

    状态转换顺序 (EnableThinking=True)::

        THINKING_START → THINKING ×N → THINKING_END → ANSWER_START → ANSWERING ×N → ANSWER_END

    状态转换顺序 (EnableThinking=False / 常规流式)::

        ANSWER_START → ANSWERING ×N → ANSWER_END
    """

    THINKING_START = "thinking_start"  # 思考开始（本帧无增量）
    THINKING       = "thinking"        # 思考中（本帧含 thinking 增量）
    THINKING_END   = "thinking_end"    # 思考结束（本帧含 thinking_snapshot）
    ANSWER_START   = "answer_start"    # 回答开始（本帧无增量）
    ANSWERING      = "answering"       # 回答中（本帧含 answer 增量）
    ANSWER_END     = "answer_end"      # 回答结束（本帧含 answer_snapshot）


class StreamOutput(BaseModel):
    """流式对话的单个事件帧。

    ``status`` 字段始终存在，可直接用于分支扬帺：

    .. code-block:: python

        async for ev in provider.invoke(msgs, stream=True):
            match ev.status:
                case StreamStatus.THINKING_START:
                    print("[\u601d\u8003\u5f00\u59cb]")
                case StreamStatus.THINKING:
                    print(ev.thinking, end="", flush=True)
                case StreamStatus.THINKING_END:
                    print("[\u601d\u8003\u7ed3\u675f]")
                case StreamStatus.ANSWER_START:
                    print("[\u56de\u7b54\u5f00\u59cb]")
                case StreamStatus.ANSWERING:
                    print(ev.answer, end="", flush=True)
                case StreamStatus.ANSWER_END:
                    print("[\u56de\u7b54\u7ed3\u675f]")

    字段语义:
      status            — 当前事件的状态标签（必填）
      thinking          — 思考内容增量片段（THINKING 帧）
      thinking_snapshot — 思考内容整体快照（THINKING_END 帧）
      answer            — 回答内容增量片段（ANSWERING 帧）
      answer_snapshot   — 回答内容整体快照（ANSWER_END 帧）
    """

    status: StreamStatus
    thinking: Optional[str] = None
    thinking_snapshot: Optional[str] = None
    answer: Optional[str] = None
    answer_snapshot: Optional[str] = None


class ResponseResult(NamedTuple):
    """非流式调用的最终结果，拍平思考部分和回答部分。"""

    thinking: str
    answer: str
