"""技能加载与上下文压缩工具，以及子 Agent 工具定义。

涵盖: LoadSkillTool, CompressTool, TaskTool
所有工具继承自 tools.base.Tool。
"""
import re
from pathlib import Path
from typing import Any

from .base import Tool


# ---------------------------------------------------------------------------
# SkillLoader（业务逻辑，保持不变）
# ---------------------------------------------------------------------------

class SkillLoader:
    def __init__(self, skills_dir: Path):
        self.skills: dict = {}
        if skills_dir.exists():
            for f in sorted(skills_dir.rglob("SKILL.md")):
                text = f.read_text()
                match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
                meta, body = {}, text
                if match:
                    for line in match.group(1).strip().splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            meta[k.strip()] = v.strip()
                    body = match.group(2).strip()
                name = meta.get("name", f.parent.name)
                self.skills[name] = {"meta": meta, "body": body}

    def descriptions(self) -> str:
        if not self.skills:
            return "(no skills)"
        return "\n".join(
            f"  - {n}: {s['meta'].get('description', '-')}"
            for n, s in self.skills.items()
        )

    def load(self, name: str) -> str:
        s = self.skills.get(name)
        if not s:
            return (
                f"Error: Unknown skill '{name}'. "
                f"Available: {', '.join(self.skills.keys())}"
            )
        return f'<skill name="{name}">\n{s["body"]}\n</skill>'


# ---------------------------------------------------------------------------
# Tool 子类
# ---------------------------------------------------------------------------

class LoadSkillTool(Tool):
    """按名称加载专业知识技能。"""

    def __init__(self, skill_loader: SkillLoader):
        self._loader = skill_loader

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def description(self) -> str:
        return "Load specialized knowledge by name."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name to load"},
            },
            "required": ["name"],
        }

    async def execute(self, name: str, **kwargs: Any) -> str:
        return self._loader.load(name)


class CompressTool(Tool):
    """手动压缩对话上下文（由 loop.py 特殊处理）。"""

    @property
    def name(self) -> str:
        return "compress"

    @property
    def description(self) -> str:
        return "Manually compress conversation context."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        # 实际压缩逻辑由 agent_loop 处理，此处返回触发信号
        return "Compressing..."


class TaskTool(Tool):
    """派生子 Agent 进行隔离探索或工作（由 loop.py 特殊处理为 async）。"""

    @property
    def name(self) -> str:
        return "task"

    @property
    def description(self) -> str:
        return "Spawn a subagent for isolated exploration or work."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Task prompt for the subagent"},
                "agent_type": {
                    "type": "string",
                    "enum": ["Explore", "general-purpose"],
                    "description": "Subagent type",
                },
            },
            "required": ["prompt"],
        }

    async def execute(self, prompt: str, agent_type: str = "Explore", **kwargs: Any) -> str:
        # 实际执行由 agent_loop 中的 run_subagent 处理
        return f"Subagent task queued: {prompt[:80]}"


# ---------------------------------------------------------------------------
# 工具实例工厂
# ---------------------------------------------------------------------------

def build_tools(skill_loader: SkillLoader) -> list[Tool]:
    return [
        LoadSkillTool(skill_loader),
        CompressTool(),
        TaskTool(),
    ]
