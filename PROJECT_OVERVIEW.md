# pyharness 项目概览

## 1. 项目用途

`pyharness` 是一个**最小化的、可测试的编码 Agent 运行框架（harness）**。它把「调用大模型 → 解析工具调用 → 执行工具 → 把结果回灌给模型」这一循环抽象成一组职责清晰、彼此解耦的模块，并附带一个可直接运行的命令行编码助手。

核心设计目标：

- **确定性可测**：所有外部依赖（模型、文件系统、时间）都可以被替换或注入，测试无需真实网络调用。
- **分层解耦**：模型协议层（`ai`）、Agent 循环层（`agent`）、应用层（`coding_agent`）单向依赖，互不反向引用。
- **安全边界**：文件工具被严格限制在指定的 workspace 目录内，拒绝绝对路径、`..` 逃逸和符号链接逃逸。
- **长会话支持**：通过 JSONL 会话持久化和上下文压缩（compaction）支持跨多次运行的长任务。

技术栈：Python ≥ 3.11，运行时依赖 `httpx`（HTTP 客户端）与 `pydantic`（数据校验/序列化），开发依赖 `pytest` 与 `pytest-asyncio`。

---

## 2. 目录结构

```
pyharness/
├── pyproject.toml          # 项目元数据、依赖、pytest 配置
├── uv.lock                 # uv 锁定的依赖版本
├── demo-session.jsonl      # 一次真实运行的 JSONL 会话示例
├── ai/                     # 模型协议层（无内部依赖）
│   ├── schemas.py
│   ├── provider.py
│   └── openai_compatible.py
├── agent/                  # Agent 循环层（仅依赖 ai）
│   ├── agent.py
│   ├── context.py
│   ├── events.py
│   ├── loop.py
│   └── tools.py
├── coding_agent/           # 应用层（依赖 ai + agent）
│   ├── builtins.py
│   ├── cli.py
│   ├── compaction.py
│   └── session.py
├── test/                   # 单元测试（pytest）
└── cli-sandbox/            # 供 CLI 试跑的空工作区目录
```

---

## 3. 模块职责

### 3.1 `ai/` —— 模型协议层

定义与「模型」交互的全部数据契约和传输实现，是整个项目的最底层。

| 文件 | 职责 |
| --- | --- |
| `ai/schemas.py` | 全部 Pydantic 数据模型：`TextPart` / `ToolCallPart`（判别联合 `AssistantPart`）、`UserMessage` / `AssistantMessage` / `ToolResultMessage`（判别联合 `Message`）、`ToolDefinition`、`ModelSpec`、`Usage`、`ChatRequest`、`ChatResponse`。所有模型 `extra="forbid"`，保证协议严格。 |
| `ai/provider.py` | `LLMProvider` 协议（`id` + `async complete`）、`ProviderError`（带 `status_code` 与 `retryable` 判定）、`ModelRegistry`（按 `(provider, id)` 路由到具体 provider），以及用于确定性测试的 `ScriptedProvider`（按脚本顺序返回预设响应并记录收到的请求）。 |
| `ai/openai_compatible.py` | `OpenAICompatibleProvider`：把内部 `ChatRequest` 序列化为 OpenAI Chat Completions 格式（system/user/assistant/tool 消息、`tool_calls`、`tools`），并把响应解析回 `ChatResponse`（含 `finish_reason` 归一化与 `usage` 提取）。支持注入 `httpx` transport 以便测试。 |

### 3.2 `agent/` —— Agent 循环层

实现「模型 ↔ 工具」的迭代循环，只依赖 `ai`，不感知具体应用场景。

| 文件 | 职责 |
| --- | --- |
| `agent/agent.py` | `AgentState`：一次运行的完整状态（system prompt、模型、工具注册表、上下文管理器、`max_steps`、完整消息历史）。`selected_messages()` 通过上下文管理器产出「发给模型的视图」，而 `messages` 始终保留完整历史。 |
| `agent/context.py` | `ContextManager`：决定哪些消息进入模型请求。默认返回全部消息；可配置 `max_messages` 只保留最近 N 条。始终返回新列表，不修改原始历史。 |
| `agent/events.py` | 运行期事件类型（`RunStarted`、`ModelRequested`、`ModelResponded`、`ToolStarted`、`ToolFinished`、`RunFinished`、`RunFailed`）与 `FailureCode` 字面量（`max_steps_exceeded` / `response_truncated` / `provider_error`）。事件为不可变 dataclass，供 UI/日志/持久化订阅。 |
| `agent/loop.py` | `AgentRunner`：核心循环。每步构造 `ChatRequest`（按 `supports_tools` 决定是否附带工具定义）→ 调用模型 → 累加 `Usage` → 若 `finish_reason == "length"` 则失败 → 若无工具调用则成功返回 → 否则逐个执行工具、把 `ToolResultMessage` 写回历史并继续。返回 `RunResult`（`final_message` / `failure` / `steps` / `usage`）。 |
| `agent/tools.py` | `AgentTool`（定义 + 参数模型 + handler）、`ToolRegistry`（注册、导出 JSON Schema 定义、执行工具调用）。执行时统一处理 `unknown_tool` / `invalid_json` / `invalid_arguments` / `tool_execution_failed` 四类错误，错误以 `ToolResultMessage(is_error=True)` 形式回灌给模型而非抛出。内置 `create_calculator_tool()`：基于 `ast` 白名单的安全算术求值器（禁止幂运算、函数调用、非数值字面量，限制表达式复杂度与结果有限性）。 |

### 3.3 `coding_agent/` —— 应用层

把 `ai` + `agent` 组装成一个可用的编码助手 CLI，并补充 workspace 工具、会话持久化与上下文压缩。

| 文件 | 职责 |
| --- | --- |
| `coding_agent/builtins.py` | workspace 受限工具：`read_file`、`write_file`、`list_dir`，以及核心安全函数 `resolve_workspace_path()`（拒绝绝对路径/盘符锚点、`..` 段、以及解析后逃出 workspace 的符号链接）。 |
| `coding_agent/session.py` | `JsonlSessionStore`：JSONL 会话持久化。首行为带版本号的 session header（`SessionMetadata`：session_id、created_at、workspace、system_prompt、model），其后每行一条消息记录。`create()` 用独占模式创建（不覆盖已有会话），`load()` 严格校验格式并抛出 `SessionFormatError`。 |
| `coding_agent/compaction.py` | 上下文压缩：`estimate_context_tokens()`（按字节数估算 token）、`should_compact()`（超过 `context_window * trigger_ratio` 触发）、`partition_history()`（保留最近 N 个完整用户轮次）、`compact_history()`（把早期历史交给 summarizer 生成摘要消息）、`ModelSummaryGenerator`（用模型生成摘要，拒绝空/截断摘要）、`CompactedContextManager`（把摘要 + 未压缩尾部作为模型视图，同时保持完整历史不变）。 |
| `coding_agent/cli.py` | 入口与编排：`parse_args()` 解析参数（task、`--workspace`、`--session`、`--provider-id`、`--model`、`--context-window`、`--base-url`、`--api-key-env`、`--system-prompt`、`--max-steps`）；`run_task()` 组装 provider/registry/tools/state，必要时先压缩恢复的历史，再运行 `AgentRunner`；`render_event()` 把事件渲染为人类可读文本；`main()` 支持注入 `provider_factory` 与 `output` 以便测试。 |

---

## 4. 依赖方向

依赖是**严格单向**的，箭头表示「依赖 / import」：

```
coding_agent  ──►  agent  ──►  ai
      │                        ▲
      └────────────────────────┘
```

- `ai/` 不 import 任何项目内其他模块（仅 `httpx`、`pydantic`、标准库）。
- `agent/` 只 import `ai/`（`agent/loop.py` 还 import `agent/agent.py`、`agent/events.py`、`agent/tools.py`）。
- `coding_agent/` import `ai/` 与 `agent/`，是唯一的「组装层」。
- 不存在反向依赖：`ai` 不知道 `agent`，`agent` 不知道 `coding_agent`。

关键抽象边界：

- **`LLMProvider` 协议**：`agent` 只依赖协议，不依赖具体 HTTP 实现，因此可以注入 `ScriptedProvider` 做测试。
- **`ContextManager` 基类**：`agent` 只依赖 `select()` 接口，`coding_agent.compaction.CompactedContextManager` 通过继承扩展，无需修改 `agent`。
- **`SummaryGenerator` 协议**：`compact_history()` 只依赖可调用对象，测试中可用 `RecordingSummarizer` 替代真实模型。
- **事件回调 `EventHandler`**：`AgentRunner` 通过 `on_event` 向外广播，CLI 用它渲染输出并写入会话文件，二者解耦。

---

## 5. 测试方法

### 5.1 运行测试

```bash
# 使用 uv（推荐，仓库已含 uv.lock）
uv run pytest

# 或使用已激活的虚拟环境
pytest

# 运行单个文件 / 单个用例
pytest test/test_loop.py
pytest test/test_cli.py::test_main_runs_workspace_tool_task_and_renders_events
```

`pyproject.toml` 中已配置 `[tool.pytest.ini_options] pythonpath = ["."]`，因此无需安装包即可从仓库根目录直接 import `ai` / `agent` / `coding_agent`。异步测试由 `pytest-asyncio` 提供，用例通过 `@pytest.mark.asyncio` 标记。

### 5.2 测试文件与覆盖范围

| 测试文件 | 覆盖内容 |
| --- | --- |
| `test/test_ai.py` | `ModelRegistry` 路由到 `ScriptedProvider`；`OpenAICompatibleProvider` 用 `httpx.MockTransport` 验证请求序列化（system/assistant/tool 消息、`tool_calls`、`tools`）与响应解析（工具调用、`finish_reason`、`usage`）。 |
| `test/test_tools.py` | `ToolRegistry` 执行路径：正常求值、`invalid_json`、`unknown_tool`、`invalid_arguments`（参数化）、不安全表达式（`x + 1`、`__import__`、`2 ** 8`）、handler 抛异常、重复注册被拒、导出的 JSON Schema 为深拷贝。 |
| `test/test_builtins.py` | `resolve_workspace_path()` 的路径安全（相对路径通过、`..` 拒绝、绝对路径拒绝、符号链接逃逸拒绝）；`read_file` / `write_file` / `list_dir` 的正常与错误路径（UTF-8 中文内容、覆盖写、缺失父目录、未知参数等）。符号链接不可用时用 `pytest.skip` 跳过。 |
| `test/test_context.py` | `ContextManager` 的裁剪语义（不修改原历史、返回新列表、非正数上限报错）与 `AgentState` 的完整历史 / 裁剪视图分离。 |
| `test/test_loop.py` | `AgentRunner` 的完整工具调用循环、事件序列、`Usage` 累加、工具错误回灌后可恢复、`max_steps_exceeded`、`response_truncated`、`provider_error` 时保留历史。 |
| `test/test_compaction.py` | `partition_history` / `estimate_context_tokens` / `should_compact` / `compact_history` / `ModelSummaryGenerator` / `CompactedContextManager` 的行为与边界，以及压缩视图与完整状态分离的端到端验证。 |
| `test/test_session.py` | JSONL 会话的创建、追加、往返一致性、拒绝覆盖、缺失文件、空文件、非法 JSON 行号、header 位置、重复 header、未知版本号。 |
| `test/test_cli.py` | 端到端 CLI：参数解析与校验、事件渲染、退出码、会话创建/恢复/跨运行续写、压缩在 Agent 运行前触发且摘要不写入会话文件。 |

### 5.3 测试策略要点

- **无真实网络**：模型调用一律通过 `ScriptedProvider`（预设响应队列）或 `httpx.MockTransport` 完成。
- **无真实文件系统副作用**：文件相关测试使用 pytest 的 `tmp_path` fixture。
- **依赖注入**：`main()` 接受 `provider_factory` 与 `output`（`StringIO`），使 CLI 可被完整测试而不触碰真实 API 或 stdout。
- **确定性**：所有消息时间戳在测试中显式指定，避免依赖当前时间。
- **参数化**：非法输入（非正数上限、非法参数 JSON、不安全表达式、非法压缩配置）用 `@pytest.mark.parametrize` 批量覆盖。
- **平台兼容**：符号链接相关用例在不可用平台上自动 skip。

### 5.4 手动试跑 CLI

```bash
export OPENAI_API_KEY=...   # 或使用 --api-key-env 指定其他变量名

uv run python -m coding_agent.cli \
  "查看工作区目录并创建 README.md" \
  --workspace ./cli-sandbox \
  --provider-id openai \
  --model gpt-4o-mini \
  --session history.jsonl \
  --context-window 128000 \
  --max-steps 10
```

`demo-session.jsonl` 是一次真实运行的会话记录，可作为 JSONL 格式与事件流的参考样例。
