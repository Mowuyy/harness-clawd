"""后台进程管理工具。

涵盖: BackgroundRunTool, CheckBackgroundTool
所有工具继承自 tools.base.Tool。
后台任务使用 asyncio.create_task + asyncio.create_subprocess_shell 实现。
"""
import asyncio
import uuid
from pathlib import Path
from typing import Any, Optional

from .base import Tool


# ---------------------------------------------------------------------------
# BackgroundManager
# ---------------------------------------------------------------------------

class BackgroundManager:
    """Run shell commands as async background tasks; drain completion notices."""

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self._tasks: dict = {}          # task_id → task metadata
        self._asyncio_tasks: dict = {}  # task_id → asyncio.Task (prevents GC)
        self._lock = asyncio.Lock()
        self._notifications: list[dict] = []

    async def run(self, command: str, timeout: int = 120) -> str:
        """Schedule a background task; return its task-id immediately."""
        tid = str(uuid.uuid4())[:8]
        async with self._lock:
            self._tasks[tid] = {"status": "running", "command": command, "result": None}
        # Hold a reference to prevent the Task from being garbage-collected.
        task = asyncio.create_task(self._exec(tid, command, timeout))
        self._asyncio_tasks[tid] = task
        return f"Background task {tid} started: {command[:80]}"

    async def _exec(self, tid: str, command: str, timeout: int) -> None:
        """Execute command asynchronously; update task state on completion."""
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workdir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = (stdout.decode() + stderr.decode()).strip()[:50000]
            result = output or "(no output)"
            status = "completed"
        except asyncio.TimeoutError:
            result = f"Timeout after {timeout}s"
            status = "error"
            # Kill the process and wait to release OS resources.
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
        except Exception as e:
            result = str(e)
            status = "error"
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass

        async with self._lock:
            self._tasks[tid].update({"status": status, "result": result})
            self._notifications.append({
                "task_id": tid,
                "status": status,
                "result": result[:500],
            })

        # Release the asyncio.Task reference now that we are done.
        self._asyncio_tasks.pop(tid, None)

    async def check(self, tid: Optional[str] = None) -> str:
        """Return status of one task (by id) or all tasks."""
        async with self._lock:
            if tid:
                t = self._tasks.get(tid)
                if t is None:
                    return f"Unknown task: {tid}"
                return f"[{t['status']}] {t.get('result', '(running)')}"
            if not self._tasks:
                return "No background tasks."
            return "\n".join(
                f"{k}: [{v['status']}] {v['command'][:60]}"
                for k, v in self._tasks.items()
            )

    def drain(self) -> list[dict]:
        """Return and clear all pending completion notifications (sync-safe)."""
        notifs, self._notifications = self._notifications, []
        return notifs


# ---------------------------------------------------------------------------
# Tool 子类
# ---------------------------------------------------------------------------

class BackgroundRunTool(Tool):
    """在后台异步任务中运行命令。"""

    def __init__(self, bg_mgr: BackgroundManager):
        self._mgr = bg_mgr

    @property
    def name(self) -> str:
        return "background_run"

    @property
    def description(self) -> str:
        return "Run a shell command as an async background task. Returns a task_id immediately."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120)",
                },
            },
            "required": ["command"],
        }

    async def execute(self, command: str, timeout: int = 120, **kwargs: Any) -> str:
        return await self._mgr.run(command, timeout)


class CheckBackgroundTool(Tool):
    """检查后台任务状态。"""

    def __init__(self, bg_mgr: BackgroundManager):
        self._mgr = bg_mgr

    @property
    def name(self) -> str:
        return "check_background"

    @property
    def description(self) -> str:
        return "Check the status of a background task by task_id, or list all tasks."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Background task ID (omit to list all)"},
            },
        }

    async def execute(self, task_id: Optional[str] = None, **kwargs: Any) -> str:
        return await self._mgr.check(task_id)


# ---------------------------------------------------------------------------
# 工具实例工厂
# ---------------------------------------------------------------------------

def build_tools(bg_mgr: BackgroundManager) -> list[Tool]:
    return [
        BackgroundRunTool(bg_mgr),
        CheckBackgroundTool(bg_mgr),
    ]
