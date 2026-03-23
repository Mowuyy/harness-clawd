"""tools 包入口：汇总所有工具实例，提供统一注册表与工具定义列表。

所有工具均继承自 tools.base.Tool，通过 to_schema() 生成
OpenAI 兼容格式，通过 execute(**kwargs) 异步执行。
"""
from .base import Tool

from . import filesystem, todos, tasks, background, messaging, team, skills, web

# Re-export managers for loop.py dependency injection
from .filesystem import (
    init as init_filesystem,
    TOOLS as FILESYSTEM_TOOLS,
)
from .todos import TodoManager
from .tasks import TaskManager
from .background import BackgroundManager
from .messaging import MessageBus, ProtocolManager, VALID_MSG_TYPES
from .team import TeammateManager
from .skills import SkillLoader


# ---------------------------------------------------------------------------
# ToolRegistry：统一管理所有工具实例
# ---------------------------------------------------------------------------

class ToolRegistry:
    """持有所有 Tool 实例，提供 schema 列表和异步分发入口。"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def register_all(self, tools: list[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        """返回 OpenAI function-calling 格式的工具定义列表。"""
        return [t.to_schema() for t in self._tools.values()]

    async def execute(self, tool_name: str, **kwargs) -> str:
        """按名称查找工具并执行，若不存在返回错误字符串。"""
        tool = self._tools.get(tool_name)
        if tool is None:
            return f"Unknown tool: {tool_name}"
        params = tool.cast_params(kwargs)
        errors = tool.validate_params(params)
        if errors:
            return f"Parameter error: {'; '.join(errors)}"
        return await tool.execute(**params)


# ---------------------------------------------------------------------------
# 工厂：根据各 Manager 实例构建完整的 ToolRegistry
# ---------------------------------------------------------------------------

def build_registry(
    todo_mgr: TodoManager,
    task_mgr: TaskManager,
    bg_mgr: BackgroundManager,
    bus: MessageBus,
    protocol: ProtocolManager,
    team_mgr: TeammateManager,
    skill_loader: SkillLoader,
    web_search_cfg: "web.WebSearchConfig | None" = None,
) -> ToolRegistry:
    """Construct a fully-populated ToolRegistry via dependency injection."""
    registry = ToolRegistry()
    registry.register_all(filesystem.TOOLS)
    registry.register(todos.TodoWriteTool(todo_mgr))
    registry.register_all(tasks.build_tools(task_mgr))
    registry.register_all(background.build_tools(bg_mgr))
    registry.register_all(
        messaging.build_tools(bus, protocol, team_mgr.member_names)
    )
    registry.register_all(team.build_tools(team_mgr))
    registry.register_all(skills.build_tools(skill_loader))
    registry.register_all(web.build_tools(search_config=web_search_cfg))
    return registry


__all__ = [
    # base
    "Tool",
    "ToolRegistry",
    # managers
    "TodoManager",
    "TaskManager",
    "BackgroundManager",
    "MessageBus",
    "ProtocolManager",
    "TeammateManager",
    "SkillLoader",
    # constants
    "VALID_MSG_TYPES",
    "FILESYSTEM_TOOLS",
    # filesystem init
    "init_filesystem",
    # factory
    "build_registry",
]
