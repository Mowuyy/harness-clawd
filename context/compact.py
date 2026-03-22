"""对话上下文压缩（s06）。

提供三种压缩策略：
  - estimate_tokens : 快速 token 数量估算
  - microcompact    : 就地清理较早的工具输出（阈值以下不操作）
  - auto_compact    : LLM 摘要压缩，返回新的消息列表
"""

import json
import time
from pathlib import Path
from typing import Any


class Compactor:
    """上下文压缩器，封装 estimate_tokens / microcompact / auto_compact。

    Args:
        llm:             LLMProvider 实例，用于生成摘要。
        transcript_dir:  压缩前保存完整对话记录的目录。
    """

    def __init__(self, llm: Any, transcript_dir: Path) -> None:
        self._llm = llm
        self._transcript_dir = transcript_dir

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_tokens(messages: list) -> int:
        """通过字节长度粗略估算 token 数（÷4）。"""
        return len(json.dumps(messages, default=str, ensure_ascii=False)) // 4

    @staticmethod
    def microcompact(messages: list) -> None:
        """就地清理较早的工具调用结果，缩减上下文。

        保留最近 3 条工具消息不变，清空更早、长度 > 100 字符的工具消息内容。
        适用于 OpenAI 格式（role=tool）消息列表。
        """
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if len(tool_msgs) <= 3:
            return
        for msg in tool_msgs[:-3]:
            if isinstance(msg.get("content"), str) and len(msg["content"]) > 100:
                msg["content"] = "[cleared]"

    async def auto_compact(self, messages: list) -> list:
        """使用 LLM 对全部对话做摘要，返回压缩后的两条消息列表。

        压缩前将完整对话以 JSONL 格式保存到 transcript_dir，
        以便事后检索。

        Returns:
            包含摘要 user 消息和 assistant 确认消息的两元素列表。
        """
        self._transcript_dir.mkdir(parents=True, exist_ok=True)
        path = self._transcript_dir / f"transcript_{int(time.time())}.jsonl"
        with open(path, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg, default=str, ensure_ascii=False) + "\n")

        conv_text = json.dumps(messages, default=str, ensure_ascii=False)[:80000]
        resp_msg = await self._llm.invoke(
            messages=[{"role": "user", "content": f"Summarize for continuity:\n{conv_text}"}],
            max_tokens=2000,
        )
        summary = resp_msg.content or ""
        return [
            {"role": "user", "content": f"[Compressed. Transcript: {path}]\n{summary}"},
            {"role": "assistant", "content": "Understood. Continuing with summary context."},
        ]
