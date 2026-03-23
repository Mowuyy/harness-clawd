"""团队消息通信工具，含关机协议与计划审批。

涵盖: MessagingTool（二级意图路由）
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

class MessagingTool(Tool):
    """通信统一入口，通过 action 二级路由分发。"""

    def __init__(
        self,
        bus: MessageBus,
        protocol: ProtocolManager,
        member_names_fn: Callable[[], list],
        lead_name: str = "lead",
    ):
        self._bus = bus
        self._protocol = protocol
        self._members_fn = member_names_fn
        self._lead = lead_name

    @property
    def name(self) -> str:
        return "messaging"

    @property
    def description(self) -> str:
        return (
            "Messaging operations with action routing: "
            "send/read_inbox/broadcast/shutdown_request/plan_approval/idle."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "send",
                        "read_inbox",
                        "broadcast",
                        "shutdown_request",
                        "plan_approval",
                        "idle",
                    ],
                    "description": "Messaging action to perform",
                },
                "to": {"type": "string", "description": "Recipient name (send)"},
                "content": {"type": "string", "description": "Message content (send/broadcast)"},
                "msg_type": {
                    "type": "string",
                    "enum": list(VALID_MSG_TYPES),
                    "description": "Message type (send)",
                },
                "teammate": {"type": "string", "description": "Teammate name (shutdown_request)"},
                "request_id": {"type": "string", "description": "Plan request ID (plan_approval)"},
                "approve": {"type": "boolean", "description": "Approve or reject (plan_approval)"},
                "feedback": {"type": "string", "description": "Review feedback (plan_approval)"},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        to: str = "",
        content: str = "",
        msg_type: str = "message",
        teammate: str = "",
        request_id: str = "",
        approve: bool = False,
        feedback: str = "",
        **kwargs: Any,
    ) -> str:
        if action == "send":
            if not to or not content:
                return "Error: to and content are required for send"
            return self._bus.send(self._lead, to, content, msg_type)

        if action == "read_inbox":
            return json.dumps(self._bus.read_inbox(self._lead), indent=2, ensure_ascii=False)

        if action == "broadcast":
            if not content:
                return "Error: content is required for broadcast"
            return self._bus.broadcast(self._lead, content, self._members_fn())

        if action == "shutdown_request":
            if not teammate:
                return "Error: teammate is required for shutdown_request"
            return self._protocol.handle_shutdown_request(teammate)

        if action == "plan_approval":
            if not request_id:
                return "Error: request_id is required for plan_approval"
            return self._protocol.handle_plan_review(request_id, approve, feedback)

        if action == "idle":
            return "Lead does not idle."

        return f"Error: Unknown action '{action}'"


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
        MessagingTool(bus, protocol, member_names_fn, lead_name),
    ]
