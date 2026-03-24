"""Harness Agent — centralised configuration via Config class.

All values can be overridden via environment variables.
Instantiate once and pass around, or use Config.default() for the singleton.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, computed_field, field_validator

# ---------------------------------------------------------------------------
# 嵌套配置模型
# ---------------------------------------------------------------------------


class LLMConfig(BaseModel):
    """LLM 供应商配置。

    provider 取值:
      openai  — 使用官方 OpenAI API 端点
      custom  — 使用自定义 OpenAI 兼容端点 (Ollama / vLLM / Azure / etc.)
    """

    provider: str = Field(default_factory=lambda: os.getenv("LLM_PROVIDER", "openai"))
    model: str = Field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-sonnet-4-5"))
    api_key: str | None = Field(default_factory=lambda: os.getenv("LLM_API_KEY") or None)
    base_url: str | None = Field(default_factory=lambda: os.getenv("LLM_BASE_URL") or None)
    max_tokens: int = Field(default_factory=lambda: int(os.getenv("HARNESS_MAX_TOKENS", "8000")))
    ssl_verify: bool = Field(
        default_factory=lambda: os.getenv("LLM_VERIFY_SSL", "false").lower() != "false"
    )

    def build_client(self):
        """构造并返回对应供应商的 ``AsyncOpenAI`` 实例（向后兼容）。"""
        return self.build_provider().build_client()

    def build_provider(self):
        """构造并返回对应供应商的 ``LLMProvider`` 实例。"""
        from llm import build_provider  # noqa: PLC0415  (lazy import)

        kwargs: dict = {"model": self.model}
        if self.provider == "openai":
            kwargs["api_key"] = self.api_key
        elif self.provider == "deepseek":
            kwargs["api_key"] = self.api_key
        else:
            kwargs.update(
                api_key=self.api_key,
                base_url=self.base_url,
                ssl_verify=self.ssl_verify,
            )
        return build_provider(self.provider, **kwargs)


class WebSearchConfig(BaseModel):
    """搜索提供商配置。"""
    provider: str = Field(default_factory=lambda: os.getenv("WEB_SEARCH_PROVIDER", "auto"))
    api_key: str | None = Field(default_factory=lambda: os.getenv("WEB_SEARCH_API_KEY") or None)
    max_results: int = Field(default_factory=lambda: int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5")))
    proxy: str | None = Field(default_factory=lambda: os.getenv("WEB_PROXY") or None)
    max_chars: int = Field(default_factory=lambda: int(os.getenv("WEB_MAX_CHARS", "50000")))


class Config(BaseModel):
    # ------------------------------------------------------------------ LLM
    llm: LLMConfig = Field(default_factory=LLMConfig)

    # ------------------------------------------------------------ Loop limits
    max_iterations: int = Field(default_factory=lambda: int(os.getenv("HARNESS_MAX_ITERATIONS", "100")))
    sub_max_iter: int = Field(default_factory=lambda: int(os.getenv("HARNESS_SUB_MAX_ITER", "30")))
    tool_result_max_chars: int = Field(default_factory=lambda: int(os.getenv("HARNESS_TOOL_RESULT_MAX_CHARS", "16000")))
    token_threshold: int = Field(default_factory=lambda: int(os.getenv("HARNESS_TOKEN_THRESHOLD", "100000")))

    # ------------------------------------------------------- Team coordination
    poll_interval: int = Field(default_factory=lambda: int(os.getenv("HARNESS_POLL_INTERVAL", "5")))
    idle_timeout: int = Field(default_factory=lambda: int(os.getenv("HARNESS_IDLE_TIMEOUT", "60")))

    # ------------------------------------------------------------------- Web
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)

    # ------------------------------------------------------------------ Paths
    user_id: str = Field(
        default_factory=lambda: os.getenv("HARNESS_USER_ID", "default")
    )
    workdir_base: Path = Field(
        default_factory=lambda: Path(os.getenv("HARNESS_WORKDIR", str(Path.cwd() / "workspace")))
    )

    @field_validator("user_id", mode="before")
    @classmethod
    def _reset_context_before_user_id(cls, v: str) -> str:
        """user_id 赋值前重置协程上下文，确保新请求不会污染上一个用户的状态。"""
        from context.context import context as _ctx_store  # noqa: PLC0415
        _ctx_store.reset()
        return v

    @computed_field  # type: ignore[misc]
    @property
    def workdir(self) -> Path:
        """用户隔离的工作目录：workdir_base / user_id。"""
        return self.workdir_base / self.user_id

    # ---------------------------------------------- Backward-compatible aliases
    @property
    def model(self) -> str:  # noqa: D102
        return self.llm.model

    @property
    def max_tokens(self) -> int:  # noqa: D102
        return self.llm.max_tokens

    @property
    def team_dir(self) -> Path:
        return self.sessions_dir / "team"

    @property
    def inbox_dir(self) -> Path:
        return self.team_dir / "inbox"

    @property
    def sessions_dir(self) -> Path:
        """Base directory for per-conversation session subdirs."""
        return self.workdir / ".sessions"

    @property
    def session_dir(self) -> "Path | None":
        """当前对话的 session 目录（由 init_session 设置，无 session 时返回 None）。"""
        return get_session_dir()

    def init_session(self, session_id: str | None = None) -> "Path":
        """为本次对话在 sessions_dir 下创建 UUID 子目录并写入上下文。

        等同于 tools.filesystem.init_session(self.sessions_dir, session_id)，
        统一由 Config 作为入口，外部代码不再直接依赖 tools.filesystem。
        """
        from tools.filesystem import init_session as _init_session  # noqa: PLC0415
        return _init_session(self.sessions_dir, session_id)

    @property
    def shared_tasks_dir(self) -> Path:
        """Teammate 共享任务池（与 per-session 任务目录区分）。"""
        return self.sessions_dir / "shared" / "tasks"

    @property
    def skills_dir(self) -> Path:
        return self.workdir / "skills"

    @property
    def transcript_dir(self) -> Path:
        return self.workdir / "transcripts"

    # --------------------------------------------------------- Factory helpers
    @classmethod
    def default(cls) -> "Config":
        """Return a Config populated entirely from environment variables."""
        return cls()

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        """Return a Config with selective overrides on top of env defaults."""
        return cls(**overrides)


# ---------------------------------------------------------------------------
# 模块级便捷函数 — 工具层（tools/）统一通过此处访问 session_dir，
# 避免直接依赖 tools.filesystem 的内部实现细节。
# ---------------------------------------------------------------------------

def get_session_dir() -> "Path | None":
    """返回当前协程上下文中活跃的 session 目录；无 session 时返回 None。

    延迟导入 tools.filesystem 以避免循环依赖。工具层（tasks.py / background.py 等）
    应从此模块导入，而非直接从 tools.filesystem 导入。
    """
    from tools.filesystem import get_session_dir as _get  # noqa: PLC0415
    return _get()


# ---------------------------------------------------------------------------
# 模块级单例 — 从环境变量读取，进程生命周期内保持不变。
# 用法: from config import config
# ---------------------------------------------------------------------------
config: Config = Config()
