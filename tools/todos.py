"""Todo 列表管理工具。

涵盖: TodoWriteTool
继承自 tools.base.Tool。
"""
from typing import Any

from .base import Tool


# ---------------------------------------------------------------------------
# TodoManager（业务逻辑，保持不变）
# ---------------------------------------------------------------------------

class TodoManager:
    def __init__(self):
        self.items: list = []

    def update(self, items: list) -> str:
        validated, ip = [], 0
        for i, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()
            af = str(item.get("activeForm", "")).strip()
            if not content:
                raise ValueError(f"Item {i}: content required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {i}: invalid status '{status}'")
            if not af:
                raise ValueError(f"Item {i}: activeForm required")
            if status == "in_progress":
                ip += 1
            validated.append({"content": content, "status": status, "activeForm": af})
        if len(validated) > 20:
            raise ValueError("Max 20 todos")
        if ip > 1:
            raise ValueError("Only one in_progress allowed")
        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."
        lines = []
        for item in self.items:
            m = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(
                item["status"], "[?]"
            )
            suffix = f" <- {item['activeForm']}" if item["status"] == "in_progress" else ""
            lines.append(f"{m} {item['content']}{suffix}")
        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)

    def has_open_items(self) -> bool:
        return any(item.get("status") != "completed" for item in self.items)


# ---------------------------------------------------------------------------
# Tool 子类
# ---------------------------------------------------------------------------

class TodoWriteTool(Tool):
    """更新任务追踪列表。"""

    def __init__(self, todo_mgr: TodoManager):
        self._mgr = todo_mgr

    @property
    def name(self) -> str:
        return "TodoWrite"

    @property
    def description(self) -> str:
        return "Update task tracking list."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                            "activeForm": {"type": "string"},
                        },
                        "required": ["content", "status", "activeForm"],
                    },
                },
            },
            "required": ["items"],
        }

    async def execute(self, items: list, **kwargs: Any) -> str:
        try:
            return self._mgr.update(items)
        except ValueError as e:
            return f"Error: {e}"

