"""context/message.py — Message assembly + system-prompt construction.

Two responsibilities, intentionally co-located:

1. ``Message``        — factory for all OpenAI chat message dicts.
2. ``ContextBuilder`` — assembles the agent system prompt dynamically
                        from runtime info, workspace bootstrap files, and
                        skill descriptions (mirrors nanobot ContextBuilder).

Usage
-----
    from context.message import Message, ContextBuilder

    msg  = Message()                            # instantiate once
    ctx  = ContextBuilder(workdir, skill_loader)

    system_prompt = ctx.build_system_prompt()   # call per LLM turn
    msg.system(system_prompt)                   # wrap for OpenAI API

Module-level ``build_*`` aliases are kept for backward compatibility.
"""

from __future__ import annotations

import platform
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tools.skills import SkillLoader


class Message:
    """Factory of OpenAI chat message dicts.

    Instantiate once and reuse; all methods are stateless helpers.
    """

    def user(self, content: str) -> dict[str, Any]:
        """``{"role": "user", "content": ...}``"""
        return {"role": "user", "content": content}

    def system(self, content: str) -> dict[str, Any]:
        """``{"role": "system", "content": ...}``"""
        return {"role": "system", "content": content}

    def ack(self, content: str) -> dict[str, Any]:
        """Plain assistant acknowledgment — no tool calls.

        ``{"role": "assistant", "content": ...}``
        """
        return {"role": "assistant", "content": content}

    def tool(self, tool_call_id: str, content: str) -> dict[str, Any]:
        """``{"role": "tool", "tool_call_id": ..., "content": ...}``"""
        return {"role": "tool", "tool_call_id": tool_call_id, "content": content}

    def from_api(self, msg: Any) -> dict[str, Any]:
        """Convert an OpenAI ``ChatCompletionMessage`` to a serialisable dict."""
        d: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        return d


# ---------------------------------------------------------------------------
# Backward-compatible module-level aliases (delegate to a shared instance)
# ---------------------------------------------------------------------------

_msg = Message()

build_user_msg = _msg.user
build_system_msg = _msg.system
build_ack_msg = _msg.ack
build_tool_msg = _msg.tool
build_assistant_msg = _msg.from_api


# ---------------------------------------------------------------------------
# ContextBuilder — dynamic system-prompt assembly
# ---------------------------------------------------------------------------

class ContextBuilder:
    """Assembles the agent system prompt on every LLM turn.

    Bootstrap section: all ``*.md`` files in the workspace root are loaded
    dynamically.  Files listed in ``PRIORITY_FILES`` are injected first and
    in the specified order; every other discovered ``*.md`` follows sorted
    alphabetically.  This means users can drop any ``*.md`` into their
    workspace and it will be picked up automatically on the next turn.
    """

    #: These files are loaded first, in declaration order, when present.
    PRIORITY_FILES: tuple[str, ...] = ("AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md")

    _TASK_GUIDELINES = """\
## Task Management
- Use `task_create` / `task_update` / `task_list` for multi-step work.
- Use `TodoWrite` for short, single-session checklists.
- Use the `task` tool to delegate work to a sub-agent.
- Use `load_skill` to load specialised knowledge before tackling a domain."""

    def __init__(self, workdir: Path, skill_loader: "SkillLoader") -> None:
        self.workdir = workdir
        self._skills = skill_loader

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_system_prompt(self) -> str:
        """Build and return the full system prompt string.

        Called fresh on every LLM turn so that skill descriptions and
        runtime context (time, etc.) are always up-to-date.
        """
        parts: list[str] = [self._identity()]

        bootstrap = self._load_bootstrap()
        if bootstrap:
            parts.append(bootstrap)

        parts.append(self._TASK_GUIDELINES)

        skills_desc = self._skills.descriptions()
        if skills_desc and skills_desc != "(no skills)":
            parts.append(f"## Available Skills\n\n{skills_desc}")

        return "\n\n---\n\n".join(parts)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _identity(self) -> str:
        """Runtime identity block: workspace, local time with timezone."""
        workspace = str(self.workdir.expanduser().resolve())

        # Use local timezone so the model sees the same time as the user.
        local_now = datetime.now().astimezone()
        tz_name = local_now.strftime("%Z")          # e.g. "CST", "PST"
        tz_offset = local_now.strftime("%z")         # e.g. "+0800"
        # Format offset as ±HH:MM for readability
        if len(tz_offset) == 5:
            tz_offset = f"{tz_offset[:3]}:{tz_offset[3:]}"
        now_str = local_now.strftime("%Y-%m-%d %H:%M") + f" {tz_name} (UTC{tz_offset})"

        return f"""\
# HarnessClawd

You are HarnessClawd, a helpful AI Assistant.

## Runtime
- Time: {now_str}

## Workspace
`{workspace}`
- Write plans and notes to files inside the workspace."""


    def _load_bootstrap(self) -> str:
        """Dynamically load all ``*.md`` files from workspace root.

        Loading order:
        1. Files in ``PRIORITY_FILES`` (if they exist), preserving declaration order.
        2. Any remaining ``*.md`` files found in the workspace root, sorted
           alphabetically, so new files are picked up without code changes.
        """
        priority_set = set(self.PRIORITY_FILES)

        # Pass 1: priority files in declared order
        ordered: list[Path] = []
        for name in self.PRIORITY_FILES:
            p = self.workdir / name
            if p.is_file():
                ordered.append(p)

        # Pass 2: remaining *.md files sorted alphabetically
        extra = sorted(
            p for p in self.workdir.glob("*.md")
            if p.name not in priority_set
        )
        all_files = ordered + extra

        parts: list[str] = []
        for path in all_files:
            try:
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"## {path.name}\n\n{content}")
            except OSError:
                pass
        return "\n\n".join(parts)
