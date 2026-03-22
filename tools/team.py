"""Teammate（多 Agent 团队）管理工具。

涵盖: SpawnTeammateTool, ListTeammatesTool
所有工具继承自 tools.base.Tool。
"""
import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from .base import Tool
from .filesystem import TOOLS as FILESYSTEM_TOOLS
from context.message import Message


_IDLE_SENTINEL = "__idle__"  # return value that _loop treats as work-done signal


# ---------------------------------------------------------------------------
# Teammate-internal Tool subclasses
# (prefixed _ to mark them as implementation details of TeammateManager._loop)
# ---------------------------------------------------------------------------

class _IdleTool(Tool):
    """Signal that the teammate has finished current work."""

    @property
    def name(self) -> str:
        return "idle"

    @property
    def description(self) -> str:
        return "Call when current work is done to enter idle/task-claim phase."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return _IDLE_SENTINEL


class _TeammateMessageTool(Tool):
    """Send a message to another team member (sender = this teammate)."""

    def __init__(self, bus, sender: str):
        self._bus = bus
        self._sender = sender

    @property
    def name(self) -> str:
        return "send_message"

    @property
    def description(self) -> str:
        return "Send a message to another teammate."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient name"},
                "content": {"type": "string", "description": "Message content"},
            },
            "required": ["to", "content"],
        }

    async def execute(self, to: str, content: str, **kwargs: Any) -> str:
        return self._bus.send(self._sender, to, content)


class _TeammateClaimTaskTool(Tool):
    """Claim a pending task from the shared board (owner = this teammate)."""

    def __init__(self, task_mgr, owner: str):
        self._mgr = task_mgr
        self._owner = owner

    @property
    def name(self) -> str:
        return "claim_task"

    @property
    def description(self) -> str:
        return "Claim a pending task from the task board."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "ID of the task to claim"},
            },
            "required": ["task_id"],
        }

    async def execute(self, task_id: int, **kwargs: Any) -> str:
        return self._mgr.claim(task_id, self._owner)


# ---------------------------------------------------------------------------
# TeammateManager（业务逻辑，保持不变）
# ---------------------------------------------------------------------------

class TeammateManager:
    """Manages a pool of autonomous agent threads, each running its own loop."""

    _MAX_WORK_ITERS = 50  # guard per work-phase

    def __init__(
        self,
        bus,
        task_mgr,
        team_dir: Path,
        tasks_dir: Path,
        llm,
        poll_interval: int = 5,
        idle_timeout: int = 60,
    ):
        self.bus = bus
        self.task_mgr = task_mgr
        self.team_dir = team_dir
        self.tasks_dir = tasks_dir
        self.llm = llm
        self.model = llm.model
        self.poll_interval = poll_interval
        self.idle_timeout = idle_timeout
        self.team_dir.mkdir(exist_ok=True)
        self.config_path = self.team_dir / "config.json"
        self.config = self._load()

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text())
        return {"team_name": "default", "members": []}

    def _save(self) -> None:
        self.config_path.write_text(json.dumps(self.config, indent=2, ensure_ascii=False))

    def _find(self, name: str) -> dict | None:
        for m in self.config["members"]:
            if m["name"] == name:
                return m
        return None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def spawn(self, name: str, role: str, prompt: str) -> str:
        member = self._find(name)
        if member:
            if member["status"] not in ("idle", "shutdown"):
                return f"Error: '{name}' is currently {member['status']}"
            member["status"] = "working"
            member["role"] = role
        else:
            member = {"name": name, "role": role, "status": "working"}
            self.config["members"].append(member)
        self._save()
        threading.Thread(
            target=self._loop_thread, args=(name, role, prompt), daemon=True
        ).start()
        return f"Spawned '{name}' (role: {role})"

    def list_all(self) -> str:
        if not self.config["members"]:
            return "No teammates."
        lines = [f"Team: {self.config['team_name']}"]
        for m in self.config["members"]:
            lines.append(f"  {m['name']} ({m['role']}): {m['status']}")
        return "\n".join(lines)

    def member_names(self) -> list:
        return [m["name"] for m in self.config["members"]]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _loop_thread(self, name: str, role: str, prompt: str) -> None:
        """每个 teammate 线程独立运行自己的 asyncio 事件循环。"""
        asyncio.run(self._loop(name, role, prompt))

    def _set_status(self, name: str, status: str) -> None:
        member = self._find(name)
        if member:
            member["status"] = status
            self._save()

    def _build_teammate_registry(
        self, name: str
    ) -> tuple[dict[str, Tool], list[dict]]:
        """Build a per-teammate tool registry (Tool instances + schemas)."""
        teammate_tools: list[Tool] = [
            *FILESYSTEM_TOOLS,
            _IdleTool(),
            _TeammateMessageTool(self.bus, name),
            _TeammateClaimTaskTool(self.task_mgr, name),
        ]
        registry = {t.name: t for t in teammate_tools}
        schemas = [t.to_schema() for t in teammate_tools]
        return registry, schemas

    async def _loop(self, name: str, role: str, prompt: str) -> None:
        team_name = self.config["team_name"]
        sys_prompt = (
            f"You are '{name}', role: {role}, team: {team_name}. "
            "Use idle when done with current work. You may auto-claim tasks."
        )
        _msg = Message()
        messages = [_msg.user(prompt)]
        _registry, tool_schemas = self._build_teammate_registry(name)

        while True:
            # ---- WORK PHASE ----
            for _ in range(self._MAX_WORK_ITERS):
                inbox = self.bus.read_inbox(name)
                for inbox_msg in inbox:
                    if inbox_msg.get("type") == "shutdown_request":
                        self._set_status(name, "shutdown")
                        return
                    messages.append(_msg.user(json.dumps(inbox_msg, ensure_ascii=False)))
                try:
                    full_msgs = [_msg.system(sys_prompt)] + messages
                    resp_msg = await self.llm.invoke(
                        messages=full_msgs,
                        tools=tool_schemas,
                        max_tokens=8000,
                    )
                except Exception:
                    self._set_status(name, "shutdown")
                    return
                messages.append(_msg.from_api(resp_msg))
                if not resp_msg.tool_calls:
                    break
                idle_requested = False
                for tc in (resp_msg.tool_calls or []):
                    tool_name = tc.function.name
                    args = tc.function.arguments
                    inp = args if isinstance(args, dict) else {}
                    tool_inst = _registry.get(tool_name)
                    if tool_inst is None:
                        output = f"Unknown tool: {tool_name}"
                    else:
                        try:
                            params = tool_inst.cast_params(inp)
                            output = await tool_inst.execute(**params)
                        except Exception as e:
                            output = f"Error: {e}"
                    if output == _IDLE_SENTINEL:
                        idle_requested = True
                    print(f"  [{name}] {tool_name}: {str(output)[:120]}")
                    messages.append(
                        _msg.tool(tc.id, str(output))
                    )
                if idle_requested:
                    break

            # ---- IDLE PHASE ----
            self._set_status(name, "idle")
            resume = False
            for _ in range(self.idle_timeout // max(self.poll_interval, 1)):
                await asyncio.sleep(self.poll_interval)
                inbox = self.bus.read_inbox(name)
                if inbox:
                    for inbox_msg in inbox:
                        if inbox_msg.get("type") == "shutdown_request":
                            self._set_status(name, "shutdown")
                            return
                        messages.append(_msg.user(json.dumps(inbox_msg, ensure_ascii=False)))
                    resume = True
                    break
                unclaimed = []
                for f in sorted(self.tasks_dir.glob("task_*.json")):
                    t = json.loads(f.read_text())
                    if (
                        t.get("status") == "pending"
                        and not t.get("owner")
                        and not t.get("blockedBy")
                    ):
                        unclaimed.append(t)
                if unclaimed:
                    task = unclaimed[0]
                    self.task_mgr.claim(task["id"], name)
                    if len(messages) <= 3:
                        messages.insert(0, _msg.user(
                            f"<identity>You are '{name}', role: {role}, team: {team_name}.</identity>"
                        ))
                        messages.insert(1, _msg.ack(f"I am {name}. Continuing."))
                    messages.append(_msg.user(
                        f"<auto-claimed>Task #{task['id']}: {task['subject']}\n"
                        f"{task.get('description', '')}</auto-claimed>"
                    ))
                    messages.append(_msg.ack(f"Claimed task #{task['id']}. Working on it."))
                    resume = True
                    break
            if not resume:
                self._set_status(name, "shutdown")
                return
            self._set_status(name, "working")


# ---------------------------------------------------------------------------
# Tool 子类
# ---------------------------------------------------------------------------

class SpawnTeammateTool(Tool):
    """派生持久化自主队友。"""

    def __init__(self, team_mgr: TeammateManager):
        self._mgr = team_mgr

    @property
    def name(self) -> str:
        return "spawn_teammate"

    @property
    def description(self) -> str:
        return "Spawn a persistent autonomous teammate."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Teammate name"},
                "role": {"type": "string", "description": "Teammate role description"},
                "prompt": {"type": "string", "description": "Initial task prompt"},
            },
            "required": ["name", "role", "prompt"],
        }

    async def execute(self, name: str, role: str, prompt: str, **kwargs: Any) -> str:
        return self._mgr.spawn(name, role, prompt)


class ListTeammatesTool(Tool):
    """列出所有队友及其状态。"""

    def __init__(self, team_mgr: TeammateManager):
        self._mgr = team_mgr

    @property
    def name(self) -> str:
        return "list_teammates"

    @property
    def description(self) -> str:
        return "List all teammates."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return self._mgr.list_all()


# ---------------------------------------------------------------------------
# 工具实例工厂
# ---------------------------------------------------------------------------

def build_tools(team_mgr: TeammateManager) -> list[Tool]:
    return [
        SpawnTeammateTool(team_mgr),
        ListTeammatesTool(team_mgr),
    ]