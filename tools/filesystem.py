"""文件系统与 Shell 执行工具。

涵盖: BashTool, ReadFileTool, WriteFileTool, EditFileTool
所有工具继承自 tools.base.Tool。
"""
import asyncio
import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Optional

from .base import Tool
from context.context import context

logger = logging.getLogger(__name__)

_WORKDIR_KEY = "workdir"
_SESSION_DIR_KEY = "session_dir"

# templates 目录位于包根目录（tools/ 的上一级）
_TEMPLATES_DIR: Path = Path(__file__).parent.parent / "templates"


def get_workdir() -> Path:
    """返回当前协程上下文的工作目录。未初始化时回退到进程 cwd。"""
    return context.get(_WORKDIR_KEY, Path.cwd())


def get_session_dir() -> Path | None:
    """返回当前对话的 session 目录；未调用 init_session 时返回 None。"""
    return context.get(_SESSION_DIR_KEY, None)


def init_session(sessions_base: Path, session_id: str | None = None) -> Path:
    """在 sessions_base/{session_id} 下创建本次对话的目录并写入 context。

    sessions_base 对应 Config.sessions_dir（即 workdir/sessions）。
    session_id 默认生成 uuid4；目录不存在时自动创建。
    返回实际创建的 session 目录路径。
    """
    sid = session_id or str(uuid.uuid4())
    session_dir = sessions_base / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    context.set(_SESSION_DIR_KEY, session_dir)
    logger.info("Session dir: %s", session_dir)
    return session_dir


def cleanup_empty_session() -> None:
    """If the current session dir contains no files, remove it.

    Called after each conversation turn so that sessions that produced no
    file output don't leave empty UUID directories behind.
    """
    session_dir: Path | None = context.get(_SESSION_DIR_KEY, None)
    if session_dir is None or not session_dir.exists():
        return
    # any() short-circuits on the first file found
    has_files = any(True for _ in session_dir.rglob("*") if _.is_file())
    if not has_files:
        try:
            shutil.rmtree(session_dir)
            logger.info("Removed empty session dir: %s", session_dir)
        except Exception as exc:
            logger.warning("Failed to remove empty session dir %s: %s", session_dir, exc)


def init(workdir: Path) -> None:
    """初始化文件系统工作目录（写入当前协程上下文）。

    若 workdir 不存在，则先创建目录，再将 templates/ 中的所有内容复制进去。
    """
    if not workdir.exists():
        workdir.mkdir(parents=True, exist_ok=True)
        if _TEMPLATES_DIR.is_dir():
            for src in _TEMPLATES_DIR.iterdir():
                dst = workdir / src.name
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            logger.info("Initialized workdir from templates: %s", workdir)
        else:
            logger.warning("templates/ directory not found at %s, skipping copy", _TEMPLATES_DIR)
    context.set(_WORKDIR_KEY, workdir)


# ---------------------------------------------------------------------------
# 路径工具函数（供 team.py 等内部复用）
# ---------------------------------------------------------------------------

def safe_path(p: str) -> Path:
    """将相对路径解析到 workdir 下（用于 read/edit）。"""
    workdir = get_workdir()
    path = (workdir / p).resolve()
    if not path.is_relative_to(workdir):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def safe_write_path(p: str) -> Path:
    """将路径解析到 session_dir 下（用于 write）。

    session dir 未初始化时回退到 workdir。
    """
    base = get_session_dir() or get_workdir()
    path = (base / p).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"Path escapes session dir: {p}")
    return path


def safe_read_path(p: str) -> Path:
    """读取时优先从 session_dir 查找，找不到再回退到 workdir。"""
    session_dir = get_session_dir()
    if session_dir:
        candidate = (session_dir / p).resolve()
        if candidate.is_relative_to(session_dir) and candidate.exists():
            return candidate
    return safe_path(p)


# ---------------------------------------------------------------------------
# 向后兼容的同步辅助函数（供子 agent / team 内部复用）
# ---------------------------------------------------------------------------

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        cwd = get_session_dir() or get_workdir()
        r = subprocess.run(
            command, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=120,
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: Optional[int] = None) -> str:
    try:
        lines = safe_read_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_write_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        c = fp.read_text()
        if old_text not in c:
            return f"Error: Text not found in {path}"
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tool 子类
# ---------------------------------------------------------------------------

class BashTool(Tool):
    """异步执行 Shell 命令。"""

    _DANGEROUS = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "Run a shell command in the workspace directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120)",
                    "minimum": 1,
                    "maximum": 600,
                },
            },
            "required": ["command"],
        }

    async def execute(self, command: str, timeout: int = 120, **kwargs: Any) -> str:
        if any(d in command for d in self._DANGEROUS):
            return "Error: Dangerous command blocked"
        try:
            # Run in session dir (same as where write operations land),
            # falling back to workdir when no session is active.
            cwd = get_session_dir() or get_workdir()
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"Error: Timeout ({timeout}s)"
            out = (
                stdout.decode("utf-8", errors="replace")
                + stderr.decode("utf-8", errors="replace")
            ).strip()
            return out[:50000] if out else "(no output)"
        except Exception as e:
            return f"Error: {e}"


class ReadFileTool(Tool):
    """读取工作区内文件内容。"""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read file contents from the workspace."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (relative to workspace)"},
                "limit": {"type": "integer", "description": "Max lines to return"},
            },
            "required": ["path"],
        }

    async def execute(self, path: str, limit: Optional[int] = None, **kwargs: Any) -> str:
        try:
            lines = safe_path(path).read_text().splitlines()
            if limit and limit < len(lines):
                lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
            return "\n".join(lines)[:50000]
        except Exception as e:
            return f"Error: {e}"


class WriteFileTool(Tool):
    """将内容写入工作区文件。"""

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file in the workspace."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (relative to workspace)"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        try:
            fp = safe_path(path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)
            return f"Wrote {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error: {e}"


class EditFileTool(Tool):
    """在工作区文件中精准替换文本。"""

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "Replace exact text in a file (first occurrence)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (relative to workspace)"},
                "old_text": {"type": "string", "description": "Exact text to replace"},
                "new_text": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_text", "new_text"],
        }

    async def execute(self, path: str, old_text: str, new_text: str, **kwargs: Any) -> str:
        try:
            fp = safe_path(path)
            c = fp.read_text()
            if old_text not in c:
                return f"Error: Text not found in {path}"
            fp.write_text(c.replace(old_text, new_text, 1))
            return f"Edited {path}"
        except Exception as e:
            return f"Error: {e}"


class WorkspaceFileTool(Tool):
    """文件操作统一入口，通过 action 在 read/write/edit 间路由。"""

    @property
    def name(self) -> str:
        return "workspace_file"

    @property
    def description(self) -> str:
        return "Workspace file operations with action routing: read/write/edit."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "edit"],
                    "description": "File action to perform",
                },
                "path": {"type": "string", "description": "File path (relative to workspace)"},
                "limit": {"type": "integer", "description": "Max lines to return (read)"},
                "content": {"type": "string", "description": "Content to write (write)"},
                "old_text": {"type": "string", "description": "Exact text to replace (edit)"},
                "new_text": {"type": "string", "description": "Replacement text (edit)"},
            },
            "required": ["action", "path"],
        }

    async def execute(
        self,
        action: str,
        path: str,
        limit: Optional[int] = None,
        content: str = "",
        old_text: str = "",
        new_text: str = "",
        **kwargs: Any,
    ) -> str:
        if action == "read":
            return run_read(path, limit)

        if action == "write":
            return run_write(path, content)

        if action == "edit":
            if not old_text:
                return "Error: old_text is required for edit"
            return run_edit(path, old_text, new_text)

        return f"Error: Unknown action '{action}'"


# ---------------------------------------------------------------------------
# 工具实例（供 __init__.py 汇总）
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    BashTool(),
    WorkspaceFileTool(),
]
