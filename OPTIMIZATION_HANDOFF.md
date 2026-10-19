# pyharness 优化开发交接

> 更新于 2026-10-19，Web 已接入 TUI 共用的真实任务执行入口；本次未进行收费模型或真实 Docker 试跑。Docker 命令沙箱已有用户异机基础验收。长期约束见 [AGENTS.md](AGENTS.md)；本文件只记录进度、取舍与阶段验收。开始新阶段前以当前代码为准。

## 目标与取舍

目标是在普通 Python 仓库中用 TUI 完成真实的基础编码任务：查找、读取、局部修改、审查 diff、运行相关测试，并能继续会话。CLI 和现有离线评测仍可用。

[mini-harness](https://github.com/mini-harness/mini-harness) 的小核心、Pydantic 工具和[单文件 Textual TUI](https://github.com/mini-harness/mini-harness/blob/main/tui.py)值得参考；它的九种工具、子 Agent、基准适配器和整套界面交互不作为本项目的交付清单。复用 pyharness 已有的 Provider、事件、Session 和 `run_task()`，只在实际出现重复装配时抽取共用函数，不预先建立通用服务或策略框架。

保留六个阶段用于记录进度，但把“先做真实任务，再完善体验”作为顺序。每阶段只实现必需能力，验证一条成功路径和关键失败路径；影响数据或权限的边界仍按 `AGENTS.md` 处理。

## 当前进度

### 2026-10-19：修复本机前端依赖安装中断

- 用户默认 Node 18.20.8 低于前端要求；已确认旧 Vite preview 进程 11640 加载了报错的 Rollup 原生模块，停止该项目预览后解除文件占用。未停止 Python 网页服务，也未更改系统默认 Node。
- 使用本机已有 Node 24.19.0 重新执行 `cd frontend` 后的 `npm ci` 与 `npm run build`，均成功；构建包含 TypeScript 检查。补充前端 README 的版本检查、Windows 文件占用和安装中断说明。本次无运行时代码修改，未重跑 Python 或浏览器测试，也未调用真实模型。

### 2026-10-19：Web 接入真实 Python Agent（尚未提交）

- 新增 `coding_agent/web.py` 和 `lario-web` 入口，通过 FastAPI/Uvicorn 提供本机 HTTP/SSE 和构建后的前端。复用 `run_task()`、真实 OpenAI-compatible Provider、工具注册、上下文预算、只读子 Agent、可选 MemoryCore 与 JSONL v2；界面不实现 Agent 决策。删除前端演示 reducer 和固定回复，保留已确认的首页及工作台风格。
- 从 TUI 提取 `coding_agent/settings.py`，两端共用原 `.runtime/lario.json` 格式。网页支持模型/Provider/API 地址/环境变量名/步数/上下文窗口/多 Agent/Session 路径设置。新建与选择会话持久化当前路径，刷新恢复服务内状态，重启恢复已保存历史；工具未知结果不自动重放。
- 审批展示真实工作区相对路径、diff 或完整命令及目录；通过异步 Future 阻塞原工具，审批绑定操作与运行 ID 且只能使用一次。停止撤销审批并等待原取消/容器清理；最后一条 SSE 连接断开 20 秒后自动停止，重连只取状态、不重放写请求。
- 只监听 `127.0.0.1`，校验 Host/Origin 与每次启动的随机连接令牌；令牌由 URL fragment 转入当前标签 sessionStorage，API Key 不进入浏览器或配置文件。启动需确认工作区信任；当前每个服务实例只允许一个活动任务。Markdown 禁止原始 HTML 与自动外部图片加载。界面展示有界窗口，完整 Session 历史不按界面截短。
- 修复此次 Windows 验收发现的既有兼容性：grep 输出统一 POSIX 分隔符；子任务历史写入在可用平台保留 `O_NOFOLLOW`/`fchmod`，Windows 继续执行路径与符号链接检查。符号链接测试只在明确的 Windows 1314 权限错误时跳过，Linux 仍实际验证。
- 最终验证（Node.js 24.19.0、Windows Python 3.11.15、WSL Ubuntu 24.04 Python 3.12.3）：
  - `uv run --locked pytest -q test/test_web.py`：14 passed。
  - Windows `uv run --locked pytest -q`：247 passed、7 skipped；跳过项涉及符号链接权限、Linux 容器引导与显式真实 Docker 测试。首次全套的 grep 路径与符号链接权限失败已按上述原因处理，最终无失败。
  - WSL `UV_PROJECT_ENVIRONMENT=/var/tmp/pyharness-web-20261019 /var/tmp/pyharness-docker-tests-20261013/bin/uv run --locked --directory /mnt/c/Users/zdl/Documents/lessons/pyharness pytest -q`：253 passed、1 skipped（显式真实 Docker 集成测试）。
  - `cd frontend` 后 `$env:LARIO_BROWSER_CHANNEL = 'msedge'; npm run test:e2e`：6 passed，包含 TypeScript 与生产构建。浏览器通过真实 HTTP/SSE、`run_task()` 和文件工具，在临时目录验证发送、审批后真实写文件、命令退出码展示、待审批刷新、设置持久化、移动端、文本转义、流式停止和主题；Provider/Docker 为测试注入，不调用收费模型或真实容器。
- 代表性测试链路：`test_web_write_requires_current_one_time_approval_and_persists_session` 从 API 到共用执行入口、diff Future、真实文件写入和 JSONL；`test_web_real_openai_adapter_with_offline_sse_transport` 经真实 OpenAI-compatible 流式适配器和 MockTransport 验证请求与增量输出；`test_web_multi_agent_uses_shared_bounded_delegate_and_saves_child_history` 验证子任务工具范围、累计步数、历史文件与重启恢复；断连/停止测试验证审批失效及未知结果恢复配对。
- 已启动本机真实服务并验证页面 200、无令牌 API 401、认证状态可读取；页面打开请求已排入应用。当前进程模型未配置、API Key 未设置，**未做真实模型试跑**。本次也未新增真实容器验收或真实编码成功率数据。配置方法、范围和限制见 [frontend/README.md](frontend/README.md)。服务从源码 `frontend/dist` 提供静态资源，发行包前端打包、公网部署、多用户与跨进程 Session 并发锁不在本次范围；不要让 TUI 与 Web 同时写入一个 Session。O6 独立新用户验收状态不变。

### 2026-10-19：增加对话输入首页（尚未提交）

- 默认显示独立的简洁首页，保留品牌、双行标题、渐变输入框与模型设置；发送后切换到原三栏工作台，首条任务带入对话并立即开始演示。新建会话回到首页，输入框自动聚焦。复用同一输入表单，没有新增依赖。
- 首页与工作台根据当前会话是否有消息切换，URL 不变，浏览器后退不用于这两个视图的切换。数据仍仅保留在页面内存中，刷新回到首页；不接入模型或 Python 后端。
- 验证：Node.js 24.19.0，`cd frontend` 后执行 `npm test`，7 passed；`$env:LARIO_BROWSER_CHANNEL = 'msedge'; npm run test:e2e`，5 passed，含 TypeScript 检查与生产构建。已查看桌面首页、390px 首页及发送后的工作台截图；本机 5173 预览返回 200 并提供最新构建。
- 代表性链路：首页发送按钮 / Enter → 共用表单 → reducer 保存一条用户消息 → 工作台及审批闭环；新建会话 → 空会话 → 首页；Shift+Enter 与中文输入法确认维持输入状态。浏览器测试另覆盖窄屏导航、文本转义、设置校验、渐变与减少动画。未修改 Python，也未运行 Python 测试或真实模型验收；字体仍在测试中使用离线系统回退。

### 2026-10-19：按用户参考调整 Web 风格（尚未提交）

- 将首版工作台改为用户提供参考中的近黑背景、灰色细线、白色胶囊按钮、黄至青绿的输入框底光与发送光环；新增 24 条射线的内联 SVG 标志及空会话双行标题。沿用现有三栏工作台、审批、停止、设置和窄屏交互，不改变演示状态机与 Python 运行逻辑。
- 按参考加载 Google Fonts Inter 400/500/600，失败时回退到系统字体；没有新增依赖。支持减少动画偏好，禁用标题入场与光环旋转。
- 验证：使用 Node.js 24.19.0、Edge，`cd frontend` 后执行 `$env:LARIO_BROWSER_CHANNEL = 'msedge'; npm run test:e2e`，生产构建及 TypeScript 检查通过，5 个浏览器测试通过；已查看桌面、空会话、390px 窄屏截图。字体请求在测试中被本地替换，不访问外网；截图展示的是系统回退字体，未验证 Google Fonts 的实际可达性。
- 代表性链路：审批按钮 → 原演示状态机 → 命令示例输出；主题测试从新会话入口检查近黑画布、渐变输入框、24 条射线及减少动画模式。没有修改 Python 代码，未运行 Python 测试；仍未接入真实 Agent。

### 2026-10-19：第一版 Lario Web 界面（尚未提交）

- 按用户确认的方案完成第一阶段：新增独立 `frontend/`，使用 React、TypeScript、Vite，提供三栏工作台、会话导航、固定示例的逐步回复、工具步骤、diff/命令审批、停止、模型设置和窄屏抽屉。
- 全部数据为明确标记的演示数据，仅存页面内存，刷新重置。未接入 Python、模型、文件工具、Docker 或真实 Session；未修改既有 CLI/TUI 与 Python 依赖。审批有次序与一次性检查，停止后迟到事件无效，运行期间不能切换会话。
- 验证使用应用自带的 Node.js 24.19.0 和本机 Edge：`cd frontend` 后执行 `npm test`（6 passed）、`npm run build`（TypeScript 与生产构建通过）、`$env:LARIO_BROWSER_CHANNEL = 'msedge'; npm run test:e2e`（4 passed）。测试不调用真实模型或外部服务。启动、验证和使用步骤见 [frontend/README.md](frontend/README.md)。
- 本机默认 Node 为 18.20.8，不满足前端要求（22.18+）；受限环境首次 npm/浏览器启动遇到 `EPERM: lstat C:\Users\zdl`。依赖安装和无界面浏览器检查经授权在限制外完成。开发服务器首屏截图检查出现过加载超时，浏览器验收已改为构建后用 preview 检查最终产物，并保留失败轨迹。
- 代表性链路：页面批准修改 → 演示状态机 → 命令审批 → 预置输出；停止 → 清除待审批状态 → 拒绝迟到操作；窄屏任务输入 → React 文本渲染，验证输入文本不会被当作 HTML。界面测试另覆盖多行输入、输入法 Enter 和设置校验。
- 未运行 Python 全套：本次未修改 Python 代码与测试。没有真实 Agent 端到端验收或新的编码成功率数据。下一步是按已讨论方案接入 `run_task()`、HTTP/SSE 与真实 Session；接入真实写入前补全本机访问认证、来源校验、审批失效和断线恢复。本次不改变 O6 的状态。

2026-10-15 提交前复核与用户异机验收（尚未提交）：

- 本机 Windows：`uv run --locked pytest -q test/test_command.py test/test_cli.py test/test_tui.py`，59 passed、3 skipped。
- 本机 Windows 全套：`uv run --locked pytest -q`，231 passed、6 skipped、3 failed。失败仍为 grep 路径分隔符、子任务历史使用 POSIX API、创建符号链接缺少权限；相关业务代码与测试本次均未修改，Windows 全套尚未通过。
- 本机 Ubuntu 24.04 WSL 全套：`UV_PROJECT_ENVIRONMENT=/var/tmp/pyharness-docker-tests-20261013/project /var/tmp/pyharness-docker-tests-20261013/bin/uv run --locked --directory /mnt/c/Users/zdl/Documents/lessons/pyharness pytest -q`，239 passed、1 skipped（真实 Docker 集成测试）。
- 用户另一台 Windows 电脑：Docker Desktop 4.95.0、Engine 29.8.2、`desktop-linux` / `linux/amd64`，用户确认已构建沙箱镜像。设置 `LARIO_TEST_DOCKER=1` 后运行 `uv run --locked pytest -q test/test_command.py -k real_docker`，用户报告 1 passed，覆盖非 root、文件副本隔离、只读输入/根文件系统、禁网和实际退出码。
- 用户提供的 Lario 工具轨迹：通过 `run_command` 执行 Python 身份与目录检查，返回 `sandbox-ok`、UID 65534、`/workspace` 和退出码 0；随后执行 `python -m pytest -q test/test_command.py`，返回 25 passed、1 skipped、退出码 0。沙箱内未传入 `LARIO_TEST_DOCKER`，因此真实 Docker 集成项跳过。以上异机结果由用户提供，未在本机重复执行真实容器或真实模型调用；模型、参数和成本未提供。
- 代表性链路：快照测试通过 `ToolRegistry.execute()` 检查参数、排除项、只读挂载和容器限制；取消测试覆盖 create/start 两阶段清理；TUI 测试覆盖命令审批和退出码显示；用户异机轨迹补充实际模型到容器执行及输出展示。以上为基础功能验收，不代表真实编码任务成功率。

2026-10-13 将 `run_command` 从 macOS `sandbox-exec` 迁移到 Linux Docker
沙箱。采用用户确认的工作区副本方案：主机快照只读挂载，容器内 tmpfs
副本可写，命令修改与产物不回写；原有文件工具的 diff 审批仍作用于主机。
保留命令审批、路径校验、30 秒超时、32 KB 输出和未知结果不重放，新增
非 root、禁网、只读根文件系统、CPU/内存/进程数与 tmpfs 上限。超时或取消
时删除实际容器，缺少 Docker/镜像时拒绝执行。镜像构建与运行前提见 README。
本次验证（2026-10-13，未提交）：

- Windows Python 3.11：`uv run --locked pytest -q test/test_command.py test/test_cli.py test/test_tui.py`，59 passed、3 skipped。跳过项为 Windows 无符号链接创建权限、Linux 容器启动脚本的 exec 行为和显式 Docker 集成测试。
- Ubuntu 24.04 WSL / Python 3.12 干净环境：`UV_PROJECT_ENVIRONMENT=/var/tmp/pyharness-docker-tests-20261013/project /var/tmp/pyharness-docker-tests-20261013/bin/uv run --locked --directory /mnt/c/Users/zdl/Documents/lessons/pyharness pytest -q`，239 passed、1 skipped（真实 Docker 集成测试）。
- Windows 首次全套 `uv run --locked pytest -q` 为 228 passed、5 skipped、4 failed；三个 Windows 旧兼容性问题在 `git archive HEAD` 的独立干净副本中复现（grep 路径分隔符、子任务历史 POSIX API、符号链接创建权限）。另一个 TUI 步骤日志测试的时序失败，单独重跑通过；Linux 全套通过。未修改这些业务模块来掩盖 Windows 基线问题。
- 代表性测试链路：`test_command_uses_isolated_snapshot_and_literal_arguments` 经 `ToolRegistry.execute()` 验证审批、快照排除、容器配置和 argv；取消测试验证 create/start 两阶段均按同一容器名清理；`test_command_returns_container_exit_code` 验证 CLI 状态与容器实际退出码分离；TUI 测试从审批界面到可见日志核对输出。

截至 2026-10-13，本机 Windows 与 WSL 均未安装 Docker；当时镜像构建、
真实容器隔离和真实模型端到端验收未运行。后续异机基础验收见上述
2026-10-15 记录，不沿用历史 macOS 验收记录。

2026-10-08 增加了可选的 Supervisor + 只读 Subagent 模式和配对评测入口。
具体实测与限制见 [多 Agent 对照](reports/MULTI_AGENT_2026-10-08.md)。
离线全套验证命令：`env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p no:capture -p pytest_asyncio.plugin -q -k 'not test_parse_args_rejects_session_path_outside_workspace'`，结果 222 passed、1 deselected。标准 `uv run --locked pytest -q` 在本机仍以退出码 139 结束，无测试断言输出。

| 阶段 | 状态 | 交付重点 |
| --- | --- | --- |
| O1 评测口径 | 功能已完成，标准环境待复验 | `52c1be8` 已区分运行、产物、任务成功和独立工具覆盖；反例测试与 README 已更新。 |
| O2 Session 与上下文 | 功能已完成，标准环境待复验 | `c401242` 已加入 JSONL v2 恢复、v1 迁移、配置校验、逐次请求预算和压缩失败处理。 |
| O3 CLI 编码闭环 | 已完成 | 已加入有界搜索、按行读取、局部编辑、diff 审批和冲突保护；离线测试已通过。 |
| O4 最小 TUI | 已完成 | Textual TUI 主链路、审批、停止、会话显示和交互样式已完成；离线回归通过，并已完成真实模型试跑。 |
| O5 必要运行增强 | 已完成 | 流式文本、有限重试和沙箱终端工具已实现；用户已完成真实 TUI 验收。 |
| O6 真实任务验收 | 进行中 | 三个历史快照的真实模型任务已逐项记录；一个完成 TUI 内测试闭环，一个仅产物通过，一个未完成。快速开始已补充，独立新用户验收待做。 |

O3 的离线测试已通过。此前本机默认 `uv` 缓存和 pytest capture 曾导致环境错误；用户已在本机确认测试通过。

O4 已在 `coding_agent/tui.py` 完成：复用 `run_task()`、Session 和现有事件，支持多行任务输入、Markdown 回答、默认隐藏且可切换的步骤日志、diff 审批、停止运行和连续会话。界面使用 Lario 蓝色主题和终端默认背景，欢迎页、工作区路径、相对 Session 路径、输入区和按钮布局已完成打磨；Textual 已加入并锁定到项目依赖。

O4 的提交包括 `1675ea7`、`5a83617`、`3b4dcce` 和 `b95d430`。用户已使用 OpenAI-compatible Provider 完成真实 TUI 编码任务试跑，并根据实际输出完成界面修正。

O4 当前验证记录：`test/test_tui.py` 为 6 passed；受限环境全套回归为 190 passed、1 deselected。验证命令为 `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p no:capture -p pytest_asyncio.plugin -q -k 'not test_parse_args_rejects_session_path_outside_workspace'`。测试覆盖参数解析、任务执行、Markdown 渲染、步骤显示切换、多行输入、布局边界、取消运行和拒绝补丁。

## 已完成阶段

### O3：先让 CLI 做成一件真实编码任务

- 在现有工作区工具上加入了有结果上限的文件/文本搜索、按行读取，以及基于唯一旧内容匹配的局部修改。修改前生成 unified diff，经一次性批准后应用；审批回调可由 CLI、TUI 和离线测试注入。
- 保留了现有 `read_file`、`write_file` 和 `list_dir` 的兼容性；CLI 中的 `write_file` 与 `edit_file` 都经过同一套 diff 审批和文件变更检查。
- 已覆盖搜索、行读取、审批拒绝、唯一匹配和补丁冲突测试。

### O5：按真实使用结果补运行能力

流式文本已接入 OpenAI-compatible Provider、Agent 事件和 TUI。TUI 增量显示回答；完整响应才写入 Session，停止后只显示未保存的部分回答。现有非流式 Provider 继续使用原接口。

有限重试已加入 `ModelRegistry`：仅对 HTTP 429、5xx 和连接故障再试一次，固定间隔 0.25 秒。流式请求已显示文字后不再重试，取消不重试；工具执行不在重试范围内。

`run_command` 已接入 Agent 和 TUI：模型提供参数数组与工作区内目录；界面展示完整命令和解析后的目录，批准后才由 macOS `sandbox-exec` 运行。工作目录越界在审批前由路径校验拒绝；进程运行时默认拒绝网络，写入限工作区，读取限工作区、系统目录和可执行文件运行目录。最长运行 30 秒，输出最多 32 KB。拒绝、沙箱不可用、超时与取消均有处理；Session 恢复沿用已有的未知结果错误配对，不自动重放命令。Apple 已弃用 `sandbox-exec`，因此这一路径目前仅面向可用的 macOS 环境。

终端工具当次离线验证：`test/test_command.py`、`test/test_cli.py`、`test/test_tui.py` 为 36 passed、1 deselected；受限环境全套为 206 passed、1 deselected。命令：`env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p no:capture -p pytest_asyncio.plugin -q -k 'not test_parse_args_rejects_session_path_outside_workspace'`。deselected 项依赖本机禁用的 pytest capture fixture；当次尚未进行真实模型端到端命令试跑。

2026-10-01 沙箱定向复验：`env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p no:capture -p pytest_asyncio.plugin -q test/test_command.py test/test_tui.py -k 'command or tui_approves_exact_command_and_working_directory'`，结果为 8 passed、8 deselected。覆盖 TUI 展示与批准、审批拒绝、越界目录、沙箱不可用、工作区外读取和网络拒绝、超时、输出上限及取消清理。当前没有 CPU、内存或磁盘配额；获批命令仍可修改工作区。当次未进行真实模型端到端命令试跑。

2026-10-02 用户确认已自行完成真实模型 TUI 的最后验收：Agent 选择验证命令，TUI 展示命令和工作目录，用户批准后在沙箱中执行并收到结果。具体模型、参数、命令、成本和输出未提供，因此此记录只作为功能验收，不计入可复现的真实编码任务评测。

有限重试的离线验证：`test/test_ai.py`、`test/test_loop.py`、`test/test_tui.py`、`test/test_compaction.py` 为 51 passed；受限环境全套为 198 passed、1 deselected。命令：`env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p no:capture -p pytest_asyncio.plugin -q -k 'not test_parse_args_rejects_session_path_outside_workspace'`。本机 `uv run --locked pytest -q test/test_ai.py test/test_loop.py test/test_tui.py test/test_compaction.py` 退出码为 139，仍需在正常环境复验。

本次离线验证：受影响的 `test/test_ai.py`、`test/test_loop.py`、`test/test_tui.py` 为 18 passed；受限环境全套为 195 passed、1 deselected。命令：`env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p no:capture -p pytest_asyncio.plugin -q -k 'not test_parse_args_rejects_session_path_outside_workspace'`。`uv run --locked pytest -q` 因本机 uv 缓存权限失败；正常 capture 的 `.venv/bin/python -m pytest -q` 退出码为 139；禁用 capture 后该项测试所需的 `capsys` fixture 不存在。尚未进行真实模型流式试跑。

- Agent 修改代码后，按任务选用目标项目已有的测试、构建或启动检查命令；不默认新增测试文件，也不主动运行 pytest，除非用户明确要求。常驻服务就绪验收不属于本阶段。
- 执行前在 TUI 展示完整命令和工作目录，由用户批准；只执行获批的命令，不向模型开放可任意使用的宿主机 shell。
- 命令必须在进程级沙箱中运行，限制时长和输出量；沙箱不可用时不执行。停止任务时终止子进程，将退出码和错误摘要返回 Agent；执行结果未知时不自动重放。
- 验收：TUI 可增量显示回答并停止流式请求；离线模拟一次限流后成功；Agent 可选择已有验证命令，经展示和批准后在沙箱内执行并得到有界结果。覆盖审批拒绝、超时或取消、沙箱不可用以及结果未知时不重放的关键失败路径。

## 剩余阶段

### O6：真实任务与交付

2026-10-02 已使用 `deepseek-chat` 在三个独立历史快照中完成首轮真实任务试跑，逐项结果、起始版本、模型参数、耗时与可取得的 token、测试命令和非目标改动见 [O6_REAL_TASKS.md](O6_REAL_TASKS.md)。task2 的恢复会话已通过 TUI 审批并在沙箱内运行 `test/test_eval_api.py`，结果为 6 passed。task1 的代码由终端复验通过，但当次 TUI 测试失败；task3 的工具覆盖目标未完成，运行中断。样本少且都来自同一仓库，不宣称通用编码成功率。

真实试跑发现虚拟环境符号链接、`/dev/null` 和工作区父目录元数据会阻断沙箱内 pytest；命令工具已加入对应的最小权限修复，受影响测试与受限环境回归已重新运行。尚需独立新用户按 README 完成一次从启动到恢复会话的文档验收，故 O6 未标完成。

2026-10-02 TUI 已将 `run_command` 的实际退出码和有界输出写入默认可见的会话日志，步骤日志开关仍可使用。`test/test_tui.py` 覆盖命令成功与非零退出时的显示，定向测试为 10 passed；受限环境全套为 209 passed、1 deselected。运行命令：`env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p no:capture -p pytest_asyncio.plugin -q test/test_tui.py`，以及相同参数加 `-k 'not test_parse_args_rejects_session_path_outside_workspace'` 的全套测试。被排除的用例依赖禁用的 capture fixture；本次显示改动尚未进行真实模型试跑。

后续真实任务评测应先写明验收标准并准备独立反例，区分首轮完成与反馈后完成；每轮设置步数和上下文预算，重复环境错误时停止并记录耗时、token 与费用。这些是后续评测方法，不作为当前 O6 独立新用户验收之外的开发门槛。

- 选 3–5 个可复现的通用 Python 维护任务，覆盖定位、修改和测试。记录初始版本、模型配置、任务结果、非目标改动、耗时与 token；样本少时报告逐项结果，不宣称通用编码成功率。
- 保留现有 20 个离线 case 作为框架回归。补上安装入口、快速开始、权限与进程沙箱前提、已知限制和任务复现步骤；不引入 Harbor、SWE-bench 或额外基准适配器。
- 最终验收：新用户按文档启动 TUI，在受控工作区用真实模型完成一项任务，查看补丁、测试结果并恢复会话。

## 下一步

下一步请独立新用户按 README 在受控工作区启动 TUI，完成一次修改、diff 审查、测试命令审批、查看退出码与输出，以及重启后的会话恢复；记录模型、参数、日期、成本与结果。达到这项验收后再将 O6 标为完成。每阶段完成时更新状态、提交、测试命令和真实模型试跑记录。
