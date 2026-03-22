# HarnessClawd

一个面向生产环境的轻量 LLM Agent 框架——供应商无关、全异步驱动、多 Agent 编排开箱即用。

融合 **[Claude Code](https://code.claude.com/docs/en/overview)** 与 **[open-clawd](https://github.com/openclaw/openclaw)** 的核心优势，提炼为一个干净、低依赖、可在任何环境运行的 Python 包。

---

## 目录

- [为什么选择 HarnessClawd？](#为什么选择-harnessclawd)
- [功能特性](#功能特性)
- [快速开始](#快速开始)
- [配置](#配置)
- [LLM 供应商](#llm-供应商)
- [工具系统](#工具系统)
- [REPL 命令](#repl-命令)
- [项目结构](#项目结构)

---

## 为什么选择 HarnessClawd？

### 站在巨人的肩膀上
HarnessClawd 有意吸取了两个成熟项目的精华：

- **Claude Code** — Anthropic 生产级编码 Agent。我们借鉴了其紧凑的工具调用循环、健壮的上下文压缩策略，以及将 Agent Loop 保持精简、可审计的设计原则。
- **open-clawd** — Claude Code 架构的开源重实现。我们采纳了其全异步设计、子 Agent 启动模式、团队收件箱协作模型和技能加载机制，并在此基础上扩展了完全可插拔的 LLM 供应商层。

最终成果：Claude Code 经过实战检验的 Loop 设计 + open-clawd 的开放性与可扩展性 + 零供应商绑定。

### 供应商无关，零迁移成本
只需修改一个环境变量，即可在 OpenAI、DeepSeek、Ollama、vLLM、Azure OpenAI 等任意 OpenAI 兼容服务之间自由切换。统一的 `LLMProvider.invoke()` 接口确保 Agent 业务代码无需随模型或供应商的变更而改动。

### 全异步，天然支持高并发
基于 `asyncio` 与异步 OpenAI SDK 从底层构建。子 Agent 并发执行，团队成员非阻塞通信，Custom 供应商通过 `httpx` 连接池处理高吞吐场景，无线程开销。

### 极简依赖，完全掌控
核心运行时仅依赖 `openai`、`pydantic`、`python-dotenv`、`httpx`、`json_repair`，无需任何重型 Agent 框架。Loop 逻辑对用户完全透明，可直接扩展。

### Pydantic 驱动的类型安全配置
`LLMConfig`、`WebSearchConfig`、`Config` 均为 `BaseModel`，每个字段带环境变量默认值。配置类型安全、IDE 自动补全，无需修改代码即可在运行时覆盖任意参数。

### 自动上下文压缩，长会话无忧
会话 Token 接近阈值时自动压缩历史记录，在保留关键信息的同时控制成本，无需手动裁剪上下文。

### 多 Agent 编排，开箱即用
子 Agent 启动、团队协作、收件箱消息传递均为一等公民，而非事后补丁。几行配置即可搭建多 Agent 流水线。

### 可扩展工具生态
将新工具放入 `tools/` 目录，所有 Agent 立即可用。MCP 协议桥接器支持接入外部工具服务器，无需改动核心 Loop。

---

## 功能特性

- **多供应商 LLM 支持**：OpenAI、DeepSeek（含 R1 推理链）、任意 OpenAI 兼容接口（Ollama / vLLM / Azure 等）
- **工具调用**：内置文件系统、网页搜索、子 Agent、任务管理、团队协作等工具
- **流式思考链**：内置对思考段与答案段的精细事件追踪
- **上下文压缩**：Token 超阈值时自动压缩历史，保留关键信息
- **子 Agent 编排**：支持启动独立子 Agent 完成复杂子任务
- **团队协作**：多 Agent 通过收件箱机制异步协作
- **交互式 REPL**：内置 `/compact`、`/tasks`、`/team`、`/inbox` 等快捷命令

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

`requirements.txt` 最小依赖：

```
python-dotenv>=1.0.0
openai
httpx
pydantic
json_repair
```

### 2. 配置环境变量

复制并编辑 `.env` 文件：

```bash
cp .env.example .env   # 如无示例文件，手动创建
```

最小配置（OpenAI）：

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-...
```

### 3. 启动 Agent

```bash
python loop.py
```

启动后进入交互式 REPL，直接输入任务描述即可。

---

## 配置

所有配置均通过环境变量读取，也可在代码中直接传入 `Config` 实例。

### LLM 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `openai` | 供应商：`openai` / `deepseek` / `custom` |
| `LLM_MODEL` | `claude-sonnet-4-5` | 模型名称 |
| `LLM_API_KEY` | — | API 密钥 |
| `LLM_BASE_URL` | — | 自定义端点 URL（`custom` 供应商必填） |
| `HARNESS_MAX_TOKENS` | `8000` | 单次请求最大输出 Token 数 |
| `LLM_VERIFY_SSL` | `false` | 是否验证 SSL 证书 |

### Agent 运行时配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `HARNESS_MAX_ITERATIONS` | `40` | 主 Agent 最大迭代轮次 |
| `HARNESS_SUB_MAX_ITER` | `30` | 子 Agent 最大迭代轮次 |
| `HARNESS_TOOL_RESULT_MAX_CHARS` | `16000` | 工具返回结果最大字符数 |
| `HARNESS_TOKEN_THRESHOLD` | `100000` | 触发上下文压缩的 Token 阈值 |
| `HARNESS_POLL_INTERVAL` | `5` | 团队消息轮询间隔（秒） |
| `HARNESS_IDLE_TIMEOUT` | `60` | 空闲超时（秒） |
| `HARNESS_WORKDIR` | `./workspace` | 工作目录路径 |

### 网页搜索配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `WEB_SEARCH_PROVIDER` | `auto` | 搜索供应商 |
| `WEB_SEARCH_API_KEY` | — | 搜索服务 API 密钥 |
| `WEB_SEARCH_MAX_RESULTS` | `5` | 最大搜索结果数 |
| `WEB_PROXY` | — | HTTP 代理 |
| `WEB_MAX_CHARS` | `50000` | 网页内容最大抓取字符数 |

---

## LLM 供应商

### OpenAI

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-...
```

### DeepSeek

支持 `deepseek-chat`（通用对话）和 `deepseek-reasoner`（R1 推理模型，自动解析 `reasoning_content` 思考字段）。

```dotenv
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-reasoner
LLM_API_KEY=<deepseek-api-key>
```

### Custom（OpenAI 兼容接口）

适用于 Ollama、vLLM、llama.cpp、Azure OpenAI 等任何遵循 OpenAI Chat Completions 格式的服务。

```dotenv
LLM_PROVIDER=custom
LLM_MODEL=qwen3:30b
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama          # 部分服务不需要，填任意字符串即可
LLM_VERIFY_SSL=false
```

### 代码中切换供应商

```python
from config import Config
from llm import build_provider

cfg = Config.from_env(llm={"provider": "deepseek", "model": "deepseek-reasoner"})
agent = AgentLoop(cfg)

# 或直接传入 LLMProvider 实例
provider = build_provider("custom", model="qwen3:30b", base_url="http://localhost:11434/v1")
agent = AgentLoop(llm=provider)
```

---

## 工具系统

`tools/` 目录下包含以下工具模块：

| 模块 | 说明 |
|---|---|
| `filesystem` | 文件读写、目录操作 |
| `web` | 网页搜索与内容抓取 |
| `tasks` | 任务创建、更新与查看 |
| `todos` | 待办事项管理 |
| `messaging` | Agent 间消息收发 |
| `team` | 团队 Agent 管理与协作 |
| `skills` | 技能加载与调用 |
| `background` | 后台任务执行 |
| `cron` | 定时任务调度 |
| `mcp` | MCP 协议工具接入 |

---

## REPL 命令

在交互式 REPL 中，可使用以下斜杠命令：

| 命令 | 说明 |
|---|---|
| `/compact` | 手动触发上下文压缩，释放 Token 空间 |
| `/tasks` | 查看当前任务列表 |
| `/team` | 查看团队成员状态 |
| `/inbox` | 查看收件箱消息 |

---

## 项目结构

```
harness-clawd/
├── loop.py              # AgentLoop 主类与交互式 REPL
├── config.py            # 集中配置（LLMConfig / Config）
├── requirements.txt
│
├── llm/                 # LLM 供应商抽象层
│   ├── base.py          # LLMProvider ABC（invoke / _invoke / _stream）
│   ├── types.py         # StreamStatus / StreamOutput / ResponseResult
│   ├── openai.py        # OpenAI 供应商
│   ├── deepseek.py      # DeepSeek 供应商（含 R1 推理链）
│   ├── custom.py        # 自定义 OpenAI 兼容供应商
│   └── __init__.py      # build_provider() 工厂函数
│
├── tools/               # 工具模块
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
├── context/             # 上下文管理（Compactor / ContextBuilder / Message）
├── web/                 # Web 界面（Next.js）
└── workspace/           # 运行时工作目录（Git 忽略）
```

---

## 许可证

MIT
