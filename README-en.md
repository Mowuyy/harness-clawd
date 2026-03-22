# HarnessClawd

A lightweight, extensible LLM agent framework built for production — provider-agnostic, async-first, and ready for multi-agent orchestration out of the box.

> Language versions:
> - Chinese: [README.md](README.md)
> - English: this file
> - 日本語: [README-ja.md](README-ja.md)

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

### Vision: multi-agent, production-grade, multi-tenant digital workforce
HarnessClawd is built not just as a demonstration agent, but as a production-grade digital workforce platform:

- Multi-agent orchestration: main task, sub-agent, assistant agent, team inbox scheduling built in.
- Production-grade resilience: auto context compaction, timeout/retry handling, extensible tool registry, single config layer.
- Multi-tenant isolation: one platform for multiple tenants, with separate data and config.

### Standing on the shoulders of giants
HarnessClawd takes deliberate inspiration from two proven projects:

- **Claude Code** — Anthropic's production-grade coding agent. We borrow its tight tool-call loop, robust context-compaction strategy, and the discipline of keeping the agent loop itself thin and auditable.
- **open-clawd** — An open-source re-implementation of Claude Code's architecture. We adopt its async-first design, sub-agent spawning patterns, team inbox coordination model, and skill-loading mechanism, while extending them with a fully pluggable LLM provider layer.

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
- **PSA: streaming chain-of-thought** — Fine-grained event tracking separates thinking blocks from answer tokens
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

### 2. Configure environment variables

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-...
```

### 3. Run the agent

```bash
python loop.py
```

---

## Configuration

(省略，参考 README.md)
