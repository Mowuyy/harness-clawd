"""LLM 供应商包。"""
from .base import LLMProvider
from .custom import CustomProvider
from .deepseek import DeepSeekProvider
from .openai import OpenAIProvider
from .types import (
    ContentPart,
    InvokeResult,
    Messages,
    ResponseFormat,
    ResponseResult,
    StreamOutput,
    StreamStatus,
    ToolParam,
)

__all__ = [
    # 供应商
    "LLMProvider",
    "OpenAIProvider",
    "CustomProvider",
    "DeepSeekProvider",
    "build_provider",
    # 类型别名
    "Messages",
    "ContentPart",
    "ToolParam",
    "ResponseFormat",
    "InvokeResult",
    # 数据模型
    "StreamOutput",
    "StreamStatus",
    "ResponseResult",
]


def build_provider(provider: str, **kwargs) -> LLMProvider:
    """根据 provider 名称构建对应的 LLMProvider 实例。

    Parameters
    ----------
    provider:
        ``"openai"``   — 官方 OpenAI API
        ``"custom"``   — 自定义 OpenAI 兼容端点
        ``"deepseek"`` — DeepSeek 官方 API（含 R1 推理模型）
    **kwargs:
        透传给对应供应商构造函数的参数。
    """
    match provider:
        case "openai":
            return OpenAIProvider(**kwargs)
        case "custom":
            return CustomProvider(**kwargs)
        case "deepseek":
            return DeepSeekProvider(**kwargs)
        case _:
            raise ValueError(
                f"Unknown LLM provider: {provider!r}. "
                "Supported: 'openai', 'custom', 'deepseek'."
            )
