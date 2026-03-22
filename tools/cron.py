"""Cron 工具：基于 asyncio 的任务调度器。

涵盖: CronManager, CronTool
支持调度类型:
  - every_seconds: 固定间隔重复
  - cron_expr:     标准 cron 表达式（需要 croniter）
  - at:            ISO datetime 一次性执行
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine

from .base import Tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CronManager（独立的 asyncio 调度器，不依赖 nanobot.cron）
# ---------------------------------------------------------------------------

class _Job:
    """单个调度任务的内部表示。"""

    __slots__ = ("id", "name", "kind", "message", "callback",
                 "every_seconds", "cron_expr", "tz", "at_dt",
                 "delete_after", "_task")

    def __init__(
        self,
        *,
        name: str,
        kind: str,
        message: str,
        callback: Callable[[str], Coroutine],
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at_dt: datetime | None = None,
        delete_after: bool = False,
    ) -> None:
        self.id = str(uuid.uuid4())[:8]
        self.name = name
        self.kind = kind
        self.message = message
        self.callback = callback
        self.every_seconds = every_seconds
        self.cron_expr = cron_expr
        self.tz = tz
        self.at_dt = at_dt
        self.delete_after = delete_after
        self._task: asyncio.Task | None = None


class CronManager:
    """异步任务调度器。"""

    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}

    # ------------------------------------------------------------------ add

    def add_every(self, name: str, message: str, every_seconds: int,
                  callback: Callable[[str], Coroutine]) -> _Job:
        job = _Job(name=name, kind="every", message=message,
                   every_seconds=every_seconds, callback=callback)
        self._start(job)
        return job

    def add_cron(self, name: str, message: str, cron_expr: str,
                 callback: Callable[[str], Coroutine], tz: str | None = None) -> _Job:
        job = _Job(name=name, kind="cron", message=message,
                   cron_expr=cron_expr, tz=tz, callback=callback)
        self._start(job)
        return job

    def add_at(self, name: str, message: str, at_dt: datetime,
               callback: Callable[[str], Coroutine]) -> _Job:
        job = _Job(name=name, kind="at", message=message,
                   at_dt=at_dt, callback=callback, delete_after=True)
        self._start(job)
        return job

    # ------------------------------------------------------------------ remove / list

    def remove(self, job_id: str) -> bool:
        job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        if job._task and not job._task.done():
            job._task.cancel()
        return True

    def list_jobs(self) -> list[_Job]:
        return list(self._jobs.values())

    # ------------------------------------------------------------------ internals

    def _start(self, job: _Job) -> None:
        self._jobs[job.id] = job
        task = asyncio.create_task(self._run(job))
        job._task = task

    async def _run(self, job: _Job) -> None:
        try:
            if job.kind == "every":
                await self._run_every(job)
            elif job.kind == "cron":
                await self._run_cron(job)
            elif job.kind == "at":
                await self._run_at(job)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Cron job '%s' (%s) raised: %s", job.name, job.id, e)
        finally:
            if job.delete_after:
                self._jobs.pop(job.id, None)

    async def _run_every(self, job: _Job) -> None:
        assert job.every_seconds is not None
        while True:
            await asyncio.sleep(job.every_seconds)
            await job.callback(job.message)

    async def _run_cron(self, job: _Job) -> None:
        try:
            from croniter import croniter
        except ImportError:
            logger.error("croniter not installed — cron_expr jobs unavailable. Run: pip install croniter")
            return

        assert job.cron_expr is not None
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(job.tz) if job.tz else timezone.utc

        while True:
            now = datetime.now(tz=tz)
            it = croniter(job.cron_expr, now)
            next_dt: datetime = it.get_next(datetime)
            delay = (next_dt - datetime.now(tz=tz)).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            await job.callback(job.message)

    async def _run_at(self, job: _Job) -> None:
        assert job.at_dt is not None
        now = datetime.now(tz=timezone.utc)
        target = job.at_dt.astimezone(timezone.utc) if job.at_dt.tzinfo else job.at_dt.replace(tzinfo=timezone.utc)
        delay = (target - now).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        await job.callback(job.message)


# ---------------------------------------------------------------------------
# CronTool
# ---------------------------------------------------------------------------

class CronTool(Tool):
    """定时任务工具：添加、列出、删除调度任务。"""

    def __init__(self, cron_manager: CronManager, callback: Callable[[str], Coroutine] | None = None):
        self._cron = cron_manager
        # callback 接收 message 字符串，由宿主注入（例如写入 inbox 或打印）
        self._callback: Callable[[str], Coroutine] = callback or self._default_callback
        self._in_cron: bool = False   # 防止在 cron 回调中再次调度

    @staticmethod
    async def _default_callback(message: str) -> None:
        logger.info("[cron] %s", message)

    @property
    def name(self) -> str:
        return "cron"

    @property
    def description(self) -> str:
        return "Schedule reminders and recurring tasks. Actions: add, list, remove."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "list", "remove"],
                    "description": "Action to perform",
                },
                "message": {"type": "string", "description": "Reminder message (required for add)"},
                "every_seconds": {
                    "type": "integer",
                    "description": "Repeat interval in seconds",
                },
                "cron_expr": {
                    "type": "string",
                    "description": "Cron expression, e.g. '0 9 * * *'",
                },
                "tz": {
                    "type": "string",
                    "description": "IANA timezone for cron_expr, e.g. 'Asia/Shanghai'",
                },
                "at": {
                    "type": "string",
                    "description": "ISO datetime for one-time execution, e.g. '2026-04-01T09:00:00'",
                },
                "job_id": {"type": "string", "description": "Job ID (required for remove)"},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        if action == "add":
            return self._add(message, every_seconds, cron_expr, tz, at)
        if action == "list":
            return self._list()
        if action == "remove":
            return self._remove(job_id)
        return f"Unknown action: {action}"

    def _add(
        self,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
    ) -> str:
        if self._in_cron:
            return "Error: cannot schedule new jobs from within a cron job callback"
        if not message:
            return "Error: message is required for add"
        if tz and not cron_expr:
            return "Error: tz can only be used with cron_expr"
        if tz:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(tz)
            except Exception:
                return f"Error: unknown timezone '{tz}'"

        if every_seconds:
            job = self._cron.add_every(message[:30], message, every_seconds, self._callback)
        elif cron_expr:
            job = self._cron.add_cron(message[:30], message, cron_expr, self._callback, tz=tz)
        elif at:
            try:
                at_dt = datetime.fromisoformat(at)
            except ValueError:
                return f"Error: invalid ISO datetime '{at}'"
            job = self._cron.add_at(message[:30], message, at_dt, self._callback)
        else:
            return "Error: one of every_seconds, cron_expr, or at is required"

        return f"Created job '{job.name}' (id: {job.id}, kind: {job.kind})"

    def _list(self) -> str:
        jobs = self._cron.list_jobs()
        if not jobs:
            return "No scheduled jobs."
        lines = [f"- {j.name} (id: {j.id}, kind: {j.kind})" for j in jobs]
        return "Scheduled jobs:\n" + "\n".join(lines)

    def _remove(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required for remove"
        return f"Removed job {job_id}" if self._cron.remove(job_id) else f"Job {job_id} not found"


# ---------------------------------------------------------------------------
# 工具实例工厂
# ---------------------------------------------------------------------------

def build_tools(
    cron_manager: CronManager | None = None,
    callback: Callable[[str], Coroutine] | None = None,
) -> list[Tool]:
    mgr = cron_manager or CronManager()
    return [CronTool(mgr, callback=callback)]
