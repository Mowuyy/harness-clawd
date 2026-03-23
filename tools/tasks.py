"""持久化任务管理工具（基于文件存储）。

涵盖: TaskOpsTool（二级意图路由）
所有工具继承自 tools.base.Tool。
"""
import json
from pathlib import Path
from typing import Any, Optional

from .base import Tool


# ---------------------------------------------------------------------------
# TaskManager（业务逻辑，保持不变）
# ---------------------------------------------------------------------------

class TaskManager:
    def __init__(self, tasks_dir: Path):
        self.tasks_dir = tasks_dir
        self.tasks_dir.mkdir(exist_ok=True)

    def _next_id(self) -> int:
        ids = [int(f.stem.split("_")[1]) for f in self.tasks_dir.glob("task_*.json")]
        return max(ids, default=0) + 1

    def _load(self, tid: int) -> dict:
        p = self.tasks_dir / f"task_{tid}.json"
        if not p.exists():
            raise ValueError(f"Task {tid} not found")
        return json.loads(p.read_text())

    def _save(self, task: dict) -> None:
        (self.tasks_dir / f"task_{task['id']}.json").write_text(
            json.dumps(task, indent=2, ensure_ascii=False)
        )

    def create(self, subject: str, description: str = "") -> str:
        task = {
            "id": self._next_id(),
            "subject": subject,
            "description": description,
            "status": "pending",
            "owner": None,
            "blockedBy": [],
            "blocks": [],
        }
        self._save(task)
        return json.dumps(task, indent=2, ensure_ascii=False)

    def get(self, tid: int) -> str:
        return json.dumps(self._load(tid), indent=2, ensure_ascii=False)

    def update(
        self,
        tid: int,
        status: str = None,
        add_blocked_by: list = None,
        add_blocks: list = None,
    ) -> str:
        task = self._load(tid)
        if status:
            task["status"] = status
            if status == "completed":
                for f in self.tasks_dir.glob("task_*.json"):
                    t = json.loads(f.read_text())
                    if tid in t.get("blockedBy", []):
                        t["blockedBy"].remove(tid)
                        self._save(t)
            if status == "deleted":
                (self.tasks_dir / f"task_{tid}.json").unlink(missing_ok=True)
                return f"Task {tid} deleted"
        if add_blocked_by:
            task["blockedBy"] = list(set(task["blockedBy"] + add_blocked_by))
        if add_blocks:
            task["blocks"] = list(set(task["blocks"] + add_blocks))
        self._save(task)
        return json.dumps(task, indent=2, ensure_ascii=False)

    def list_all(self) -> str:
        tasks = [
            json.loads(f.read_text())
            for f in sorted(self.tasks_dir.glob("task_*.json"))
        ]
        if not tasks:
            return "No tasks."
        lines = []
        for t in tasks:
            m = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(
                t["status"], "[?]"
            )
            owner = f" @{t['owner']}" if t.get("owner") else ""
            blocked = f" (blocked by: {t['blockedBy']})" if t.get("blockedBy") else ""
            lines.append(f"{m} #{t['id']}: {t['subject']}{owner}{blocked}")
        return "\n".join(lines)

    def claim(self, tid: int, owner: str) -> str:
        task = self._load(tid)
        task["owner"] = owner
        task["status"] = "in_progress"
        self._save(task)
        return f"Claimed task #{tid} for {owner}"


# ---------------------------------------------------------------------------
# Tool 子类
# ---------------------------------------------------------------------------

class TaskOpsTool(Tool):
    """任务操作统一入口，通过 action 二级路由分发。"""

    def __init__(self, task_mgr: TaskManager, owner: str = "lead"):
        self._mgr = task_mgr
        self._owner = owner

    @property
    def name(self) -> str:
        return "task_ops"

    @property
    def description(self) -> str:
        return "Task operations with action routing: create/get/update/list/claim."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "get", "update", "list", "claim"],
                    "description": "Task action to perform",
                },
                "subject": {"type": "string", "description": "Task subject (create)"},
                "description": {"type": "string", "description": "Task description (create)"},
                "task_id": {"type": "integer", "description": "Task ID (get/update/claim)"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "deleted"],
                    "description": "Task status (update)",
                },
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Blocked by IDs (update)",
                },
                "add_blocks": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Blocks IDs (update)",
                },
                "owner": {"type": "string", "description": "Claim owner override (claim)"},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        subject: str = "",
        description: str = "",
        task_id: Optional[int] = None,
        status: Optional[str] = None,
        add_blocked_by: Optional[list] = None,
        add_blocks: Optional[list] = None,
        owner: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        try:
            if action == "create":
                if not subject:
                    return "Error: subject is required for create"
                return self._mgr.create(subject, description)
            if action == "get":
                if task_id is None:
                    return "Error: task_id is required for get"
                return self._mgr.get(task_id)
            if action == "update":
                if task_id is None:
                    return "Error: task_id is required for update"
                return self._mgr.update(task_id, status, add_blocked_by, add_blocks)
            if action == "list":
                return self._mgr.list_all()
            if action == "claim":
                if task_id is None:
                    return "Error: task_id is required for claim"
                claim_owner = owner or self._owner
                return self._mgr.claim(task_id, claim_owner)
            return f"Error: Unknown action '{action}'"
        except Exception as e:
            return f"Error: {e}"


# ---------------------------------------------------------------------------
# 工具实例工厂（供 __init__.py 调用）
# ---------------------------------------------------------------------------

def build_tools(task_mgr: TaskManager, owner: str = "lead") -> list[Tool]:
    return [
        TaskOpsTool(task_mgr, owner),
    ]
