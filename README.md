# HarnessClawd

A lightweight, extensible LLM agent framework built for production — provider-agnostic, async-first, and ready for multi-agent orchestration out of the box.

Inspired by the best ideas from **[Claude Code](https://code.claude.com/docs/en/overview)** and **[open-clawd](https://github.com/openclaw/openclaw)**, HarnessClawd distills their strengths into a clean, dependency-light Python package you can run anywhere.

> 中文文档: [README-zh.md](README-zh.md)

---

## Table of Contents

- [Why HarnessClawd?](#why-harnessclawd)
- [Features](#features)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [LLM Providers](#llm-providers)
- [Tool System](#tool-system)
- [REPL Commands](#repl-commands)
- [Project Structure](#project-structure)

---

## Why HarnessClawd?

### Standing on the shoulders of giants
HarnessClawd takes deliberate inspiration from two proven projects:

- **Claude Code** — Anthropic's production-grade coding agent. We borrow its tight tool-call loop, robust context-compaction strategy, and the discipline of keeping the agent loop itself as thin and auditable as possible.
- **open-clawd** — An open-source re-implementation of Claude Code's architecture. We adopt its async-first design, sub-agent spawning patterns, team-inbox coordination model, and skill-loading mechanism, while extending them with a fully pluggable LLM provider layer.

The result: the battle-tested loop design of Claude Code, the openness and extensibility of open-clawd, and zero vendor lock-in.

### No vendor lock-in
Swap between OpenAI, DeepSeek, Ollama, vLLM, Azure, or any OpenAI-compatible service by changing a single environment variable. The unified `LLMProvider.invoke()` interface means your agent code never needs to change when you switch models or providers.

### Async-first, built for concurrency
Built entirely on `asyncio` and the async OpenAI SDK. Sub-agents run concurrently, team members communicate without blocking, and the connection pool on the custom provider handles high-throughput workloads without thread overhead.

### Minimal dependencies, maximum control
The core runtime needs only `openai`, `pydantic`, `python-dotenv`, `httpx`, and `json_repair`. No heavyweight agent framework required — you own the loop logic and can extend it directly.

### Pydantic-driven configuration
Every setting (`LLMConfig`, `WebSearchConfig`, `Config`) is a `BaseModel` with per-field env-var defaults. Configuration is type-safe, IDE-autocompleted, and overridable without touching code.

### Automatic context compaction
Long-running sessions automatically compress conversation history when the token threshold is approached, preserving essential context while keeping costs under control — no manual pruning needed.

### Multi-agent orchestration out of the box
Sub-agents, team coordination, and an inbox-based messaging system are first-class concepts, not afterthoughts. Stand up a multi-agent pipeline with a few lines of configuration.

### Extensible tool ecosystem
Drop new tools into `tools/` and they are immediately available to every agent. The MCP protocol bridge lets you integrate external tool servers without rewriting the core loop.

---

## Features

- **Multi-provider LLM support** — OpenAI, DeepSeek (including R1 reasoning chain), and any OpenAI-compatible endpoint (Ollama / vLLM / Azure / llama.cpp, etc.)
- **Tool calling** — Built-in tools for filesystem access, web search, sub-agents, task management, team collaboration, and more
- **Streaming chain-of-thought** — Fine-grained event tracking separates thinking blocks from answer tokens
- **Context compaction** — Automatically compresses conversation history when the token threshold is reached
- **Sub-agent orchestration** — Spawn independent sub-agents to handle complex nested tasks
- **Team collaboration** — Multiple agents communicate asynchronously via an inbox mechanism
- **Interactive REPL** — Built-in `/compact`, `/tasks`, `/team`, and `/inbox` slash commands

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

Core dependencies in `requirements.txt`:

```
python-dotenv>=1.0.0
openai
httpx
pydantic
json_repair
```

### 2. Configure environment variables

Create a `.env` file in the project root:

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-...
```

### 3. Run the agent

```bash
python loop.py
```

An interactive REPL starts — type any task description to begin.

---

## Configuration

All settings are read from environment variables. You can also construct a `Config` instance directly in code.

### LLM settings

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `openai` | Provider: `openai` / `deepseek` / `custom` |
| `LLM_MODEL` | `claude-sonnet-4-5` | Model name |
| `LLM_API_KEY` | — | API key |
| `LLM_BASE_URL` | — | Custom endpoint URL (required for `custom` provider) |
| `HARNESS_MAX_TOKENS` | `8000` | Max output tokens per request |
| `LLM_VERIFY_SSL` | `false` | Verify SSL certificates |

### Agent runtime settings

| Variable | Default | Description |
|---|---|---|
| `HARNESS_MAX_ITERATIONS` | `40` | Max iterations for the main agent loop |
| `HARNESS_SUB_MAX_ITER` | `30` | Max iterations for sub-agents |
| `HARNESS_TOOL_RESULT_MAX_CHARS` | `16000` | Max characters in a tool result |
| `HARNESS_TOKEN_THRESHOLD` | `100000` | Token count that triggers context compaction |
| `HARNESS_POLL_INTERVAL` | `5` | Team message polling interval (seconds) |
| `HARNESS_IDLE_TIMEOUT` | `60` | Idle timeout (seconds) |
| `HARNESS_WORKDIR` | `./workspace` | Runtime working directory |

### Web search settings

| Variable | Default | Description |
|---|---|---|
| `WEB_SEARCH_PROVIDER` | `auto` | Search provider |
| `WEB_SEARCH_API_KEY` | — | Search service API key |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Maximum number of search results |
| `WEB_PROXY` | — | HTTP proxy |
| `WEB_MAX_CHARS` | `50000` | Maximum characters fetched from a web page |

---

## LLM Providers

### OpenAI

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-...
```

### DeepSeek

Supports `deepseek-chat` (general) and `deepseek-reasoner` (R1 reasoning model — `reasoning_content` is automatically parsed from the delta stream).

```dotenv
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-reasoner
LLM_API_KEY=<deepseek-api-key>
```

### Custom (OpenAI-compatible)

Works with Ollama, vLLM, llama.cpp, Azure OpenAI, or any service that implements the OpenAI Chat Completions API.

```dotenv
LLM_PROVIDER=custom
LLM_MODEL=qwen3:30b
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama        # some services don't require a real key
LLM_VERIFY_SSL=false
```

### Switching providers in code

```python
from config import Config
from llm import build_provider
from loop import AgentLoop

# via Config
cfg = Config.from_env(llm={"provider": "deepseek", "model": "deepseek-reasoner"})
agent = AgentLoop(cfg)

# or inject a provider instance directly
provider = build_provider("custom", model="qwen3:30b", base_url="http://localhost:11434/v1")
agent = AgentLoop(llm=provider)
```

---

## Tool System

Tools live in the `tools/` package:

| Module | Description |
|---|---|
| `filesystem` | Read/write files and directories |
| `web` | Web search and page fetching |
| `tasks` | Create, update, and list tasks |
| `todos` | Todo item management |
| `messaging` | Send and receive messages between agents |
| `team` | Manage and coordinate team agents |
| `skills` | Load and invoke skills |
| `background` | Run tasks in the background |
| `cron` | Schedule recurring tasks |
| `mcp` | MCP protocol tool integration |

---

## REPL Commands

When running interactively, the following slash commands are available:

| Command | Description |
|---|---|
| `/compact` | Manually trigger context compaction to free up token space |
| `/tasks` | View the current task list |
| `/team` | View team member status |
| `/inbox` | View inbox messages |

---

## Project Structure

```
harness-clawd/
├── loop.py              # AgentLoop class and interactive REPL
├── config.py            # Centralised config (LLMConfig / Config)
├── requirements.txt
│
├── llm/                 # LLM provider abstraction layer
│   ├── base.py          # LLMProvider ABC (invoke / _invoke / _stream)
│   ├── types.py         # StreamStatus / StreamOutput / ResponseResult
│   ├── openai.py        # OpenAI provider
│   ├── deepseek.py      # DeepSeek provider (R1 reasoning chain)
│   ├── custom.py        # Custom OpenAI-compatible provider
│   └── __init__.py      # build_provider() factory function
│
├── tools/               # Tool modules
│   ├── filesystem.py
│   ├── web.py
│   ├── tasks.py
│   ├── todos.py
│   ├── messaging.py
│   ├── team.py
│   ├── skills.py
│   ├── background.py
│   ├── cron.py
│   ├── mcp.py
│   └── __init__.py
│
├── context/             # Context management (Compactor / ContextBuilder / Message)
├── web/                 # Web UI (Next.js)
└── workspace/           # Runtime working directory (git-ignored)
```

---

## License

MIT
