"""技能加载与上下文压缩工具，以及子 Agent 工具定义。

涵盖: SkillLoader, LoadSkillTool, CompressTool, TaskTool
所有工具继承自 tools.base.Tool。

SkillLoader 加载优先级:
  1. workspace/skills/<name>/SKILL.md  （用户工作区，最高优先级）
  2. <builtin_skills_dir>/<name>/SKILL.md （内建技能，回退）

requirements 检测:
  frontmatter 中 requires.bins / requires.env 均须满足，否则技能标记为不可用。
"""
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .base import Tool

# 默认内建技能目录：harness-clawd/skills/
_BUILTIN_SKILLS_DIR: Path = Path(__file__).parent.parent / "skills"


# ---------------------------------------------------------------------------
# SkillLoader
# ---------------------------------------------------------------------------

class SkillLoader:
    """两级技能加载器（workspace 优先于 builtin）。

    - 惰性加载：`__init__` 只记录路径，实际读取在 `load()` / `list_skills()` 时发生。
    - workspace 中同名技能会覆盖 builtin。
    - 可通过 frontmatter ``requires.bins`` / ``requires.env`` 声明依赖。
    """

    def __init__(self, skills_dir: Path, builtin_skills_dir: Path | None = None):
        self.workspace_skills = skills_dir
        self.builtin_skills: Path | None = builtin_skills_dir or (
            _BUILTIN_SKILLS_DIR if _BUILTIN_SKILLS_DIR.exists() else None
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """列出所有技能，workspace 优先于 builtin（同名仅保留 workspace 版本）。

        Returns:
            每项包含 ``name``, ``path``, ``source`` 键的字典列表。
        """
        skills: list[dict[str, str]] = []

        if self.workspace_skills.exists():
            for sd in sorted(self.workspace_skills.iterdir()):
                sf = sd / "SKILL.md"
                if sd.is_dir() and sf.exists():
                    skills.append({"name": sd.name, "path": str(sf), "source": "workspace"})

        if self.builtin_skills and self.builtin_skills.exists():
            existing = {s["name"] for s in skills}
            for sd in sorted(self.builtin_skills.iterdir()):
                sf = sd / "SKILL.md"
                if sd.is_dir() and sf.exists() and sd.name not in existing:
                    skills.append({"name": sd.name, "path": str(sf), "source": "builtin"})

        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills

    def load(self, name: str) -> str:
        """按名称加载技能内容（自动 strip frontmatter），包裹在 <skill> 标签内。

        找不到时返回带可用列表的错误字符串。
        """
        raw = self._load_raw(name)
        if raw is None:
            available = ", ".join(s["name"] for s in self.list_skills(filter_unavailable=False))
            return f"Error: Unknown skill '{name}'. Available: {available or '(none)'}"

        from context.context import context
        body = self._strip_frontmatter(raw)
        placeholders = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", body))
        if placeholders:
            fmt_vars = {k: str(context.get(k, "")) for k in placeholders}
            body = body.format_map(fmt_vars)
        return f'<skill name="{name}">\n{body}\n</skill>'

    def descriptions(self) -> str:
        """返回可用技能的单行描述列表（用于系统提示）。"""
        skills = self.list_skills(filter_unavailable=True)
        if not skills:
            return "(no skills)"
        lines = []
        for s in skills:
            meta = self.get_skill_metadata(s["name"]) or {}
            desc = meta.get("description", "-")
            lines.append(f"  - {s['name']}: {desc}")
        return "\n".join(lines)

    def build_skills_summary(self) -> str:
        """生成所有技能的 XML 摘要（含可用性与缺失依赖信息）。"""
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def _esc(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = ["<skills>"]
        for s in all_skills:
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)
            desc = _esc(meta.get("description", s["name"]))

            lines.append(f'  <skill available="{str(available).lower()}">')
            lines.append(f"    <name>{_esc(s['name'])}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{s['path']}</location>")
            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{_esc(missing)}</requires>")
            lines.append("  </skill>")
        lines.append("</skills>")
        return "\n".join(lines)

    def get_always_skills(self) -> list[str]:
        """返回 frontmatter 中标记 ``always: true`` 的技能名列表。"""
        result = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_meta = self._get_skill_meta(s["name"])
            if skill_meta.get("always") or meta.get("always", "").lower() == "true":
                result.append(s["name"])
        return result

    def get_skill_metadata(self, name: str) -> dict | None:
        """解析技能 SKILL.md 的 YAML frontmatter，返回键值字典。"""
        raw = self._load_raw(name)
        if not raw or not raw.startswith("---"):
            return None
        m = re.match(r"^---\n(.*?)\n---", raw, re.DOTALL)
        if not m:
            return None
        metadata: dict[str, str] = {}
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                metadata[k.strip()] = v.strip().strip("\"'")
        return metadata

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_raw(self, name: str) -> str | None:
        """按优先级查找并返回 SKILL.md 原始内容；找不到返回 None。"""
        ws = self.workspace_skills / name / "SKILL.md"
        if ws.exists():
            return ws.read_text(encoding="utf-8")
        if self.builtin_skills:
            bi = self.builtin_skills / name / "SKILL.md"
            if bi.exists():
                return bi.read_text(encoding="utf-8")
        return None

    def _strip_frontmatter(self, content: str) -> str:
        """移除 YAML frontmatter（--- ... ---）。"""
        if content.startswith("---"):
            m = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if m:
                return content[m.end():].strip()
        return content

    def _get_skill_meta(self, name: str) -> dict:
        """解析技能 frontmatter 中的 ``metadata`` JSON（nanobot/openclaw 格式）。"""
        meta = self.get_skill_metadata(name) or {}
        raw = meta.get("metadata", "")
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data.get("openclaw", data)
        except (json.JSONDecodeError, TypeError):
            pass
        return {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        """检查 ``requires.bins`` 命令和 ``requires.env`` 环境变量是否均已满足。"""
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """返回未满足依赖的可读描述。"""
        missing: list[str] = []
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)


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
        TaskTool(),
    ]
