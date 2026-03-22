"""团队消息通信工具，含关机协议与计划审批。

涵盖: SendMessageTool, ReadInboxTool, BroadcastTool,
       ShutdownRequestTool, PlanApprovalTool, IdleTool
所有工具继承自 tools.base.Tool。
"""
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from .base import Tool

VALID_MSG_TYPES: set = {
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
    "plan_approval_response",
}


# ---------------------------------------------------------------------------
# MessageBus（业务逻辑，保持不变）
# ---------------------------------------------------------------------------

class MessageBus:
    def __init__(self, inbox_dir: Path):
        self.inbox_dir = inbox_dir
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

    def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
        extra: dict = None,
    ) -> str:
        msg = {
            "type": msg_type,
            "from": sender,
            "content": content,
            "timestamp": time.time(),
        }
        if extra:
            msg.update(extra)
        with open(self.inbox_dir / f"{to}.jsonl", "a") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        return f"Sent {msg_type} to {to}"

    def read_inbox(self, name: str) -> list:
        path = self.inbox_dir / f"{name}.jsonl"
        if not path.exists():
            return []
        msgs = [json.loads(line) for line in path.read_text().strip().splitlines() if line]
        path.write_text("")
        return msgs

    def broadcast(self, sender: str, content: str, names: list) -> str:
        count = 0
        for n in names:
            if n != sender:
                self.send(sender, n, content, "broadcast")
                count += 1
        return f"Broadcast to {count} teammates"


# ---------------------------------------------------------------------------
# ProtocolManager（关机 & 计划审批握手协议，保持不变）
# ---------------------------------------------------------------------------

class ProtocolManager:
    """Manages shutdown-handshake and plan-approval protocols."""

    def __init__(self, bus: MessageBus):
        self.bus = bus
        self.shutdown_requests: dict[str, dict] = {}
        self.plan_requests: dict[str, dict] = {}

    def submit_plan(self, requester: str, plan: str) -> str:
        """Teammate calls this to submit a plan for lead approval."""
        req_id = str(uuid.uuid4())[:8]
        self.plan_requests[req_id] = {"from": requester, "plan": plan, "status": "pending"}
        self.bus.send(
            requester, "lead", plan, "plan_approval_request",
            {"request_id": req_id},
        )
        return req_id

    def handle_shutdown_request(self, teammate: str) -> str:
        req_id = str(uuid.uuid4())[:8]
        self.shutdown_requests[req_id] = {"target": teammate, "status": "pending"}
        self.bus.send(
            "lead", teammate, "Please shut down.", "shutdown_request",
            {"request_id": req_id},
        )
        return f"Shutdown request {req_id} sent to '{teammate}'"

    def handle_plan_review(
        self, request_id: str, approve: bool, feedback: str = ""
    ) -> str:
        req = self.plan_requests.get(request_id)
        if not req:
            return f"Error: Unknown plan request_id '{request_id}'"
        req["status"] = "approved" if approve else "rejected"
        self.bus.send(
            "lead", req["from"], feedback, "plan_approval_response",
            {"request_id": request_id, "approve": approve, "feedback": feedback},
        )
        return f"Plan {req['status']} for '{req['from']}'"


# ---------------------------------------------------------------------------
# Tool 子类
# ---------------------------------------------------------------------------

class SendMessageTool(Tool):
    """向队友发送消息。"""

    def __init__(self, bus: MessageBus, lead_name: str = "lead"):
        self._bus = bus
        self._lead = lead_name

    @property
    def name(self) -> str:
        return "send_message"

    @property
    def description(self) -> str:
        return "Send a message to a teammate."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient name"},
                "content": {"type": "string", "description": "Message content"},
                "msg_type": {
                    "type": "string",
                    "enum": list(VALID_MSG_TYPES),
                    "description": "Message type",
                },
            },
            "required": ["to", "content"],
        }

    async def execute(
        self, to: str, content: str, msg_type: str = "message", **kwargs: Any
    ) -> str:
        return self._bus.send(self._lead, to, content, msg_type)


class ReadInboxTool(Tool):
    """读取并清空 lead 的收件箱。"""

    def __init__(self, bus: MessageBus, lead_name: str = "lead"):
        self._bus = bus
        self._lead = lead_name

    @property
    def name(self) -> str:
        return "read_inbox"

    @property
    def description(self) -> str:
        return "Read and drain the lead's inbox."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return json.dumps(self._bus.read_inbox(self._lead), indent=2, ensure_ascii=False)


class BroadcastTool(Tool):
    """向所有队友广播消息。"""

    def __init__(self, bus: MessageBus, member_names_fn: Callable[[], list], lead_name: str = "lead"):
        self._bus = bus
        self._members_fn = member_names_fn
        self._lead = lead_name

    @property
    def name(self) -> str:
        return "broadcast"

    @property
    def description(self) -> str:
        return "Send message to all teammates."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Broadcast message content"},
            },
            "required": ["content"],
        }

    async def execute(self, content: str, **kwargs: Any) -> str:
        return self._bus.broadcast(self._lead, content, self._members_fn())


class ShutdownRequestTool(Tool):
    """请求队友关闭。"""

    def __init__(self, protocol: ProtocolManager):
        self._protocol = protocol

    @property
    def name(self) -> str:
        return "shutdown_request"

    @property
    def description(self) -> str:
        return "Request a teammate to shut down."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "teammate": {"type": "string", "description": "Teammate name to shut down"},
            },
            "required": ["teammate"],
        }

    async def execute(self, teammate: str, **kwargs: Any) -> str:
        return self._protocol.handle_shutdown_request(teammate)


class PlanApprovalTool(Tool):
    """批准或拒绝队友的计划。"""

    def __init__(self, protocol: ProtocolManager):
        self._protocol = protocol

    @property
    def name(self) -> str:
        return "plan_approval"

    @property
    def description(self) -> str:
        return "Approve or reject a teammate's plan."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "approve": {"type": "boolean"},
                "feedback": {"type": "string"},
            },
            "required": ["request_id", "approve"],
        }

    async def execute(self, request_id: str, approve: bool, feedback: str = "", **kwargs: Any) -> str:
        return self._protocol.handle_plan_review(request_id, approve, feedback)


class IdleTool(Tool):
    """进入空闲状态（Lead 不使用此工具）。"""

    @property
    def name(self) -> str:
        return "idle"

    @property
    def description(self) -> str:
        return "Enter idle state."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "Lead does not idle."


# ---------------------------------------------------------------------------
# 工具实例工厂
# ---------------------------------------------------------------------------

def build_tools(
    bus: MessageBus,
    protocol: ProtocolManager,
    member_names_fn: Callable[[], list],
    lead_name: str = "lead",
) -> list[Tool]:
    return [
        SendMessageTool(bus, lead_name),
        ReadInboxTool(bus, lead_name),
        BroadcastTool(bus, member_names_fn, lead_name),
        ShutdownRequestTool(protocol),
        PlanApprovalTool(protocol),
        IdleTool(),
    ]
