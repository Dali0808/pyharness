# pyharness 优化开发交接

> 更新于 2026-10-02，O5 的流式文本、有限重试和沙箱终端工具已开发并完成用户验收。长期约束见 [AGENTS.md](AGENTS.md)；本文件只记录进度、取舍与阶段验收。开始新阶段前以当前代码为准。

## 目标与取舍

目标是在普通 Python 仓库中用 TUI 完成真实的基础编码任务：查找、读取、局部修改、审查 diff、运行相关测试，并能继续会话。CLI 和现有离线评测仍可用。

[mini-harness](https://github.com/mini-harness/mini-harness) 的小核心、Pydantic 工具和[单文件 Textual TUI](https://github.com/mini-harness/mini-harness/blob/main/tui.py)值得参考；它的九种工具、子 Agent、基准适配器和整套界面交互不作为本项目的交付清单。复用 pyharness 已有的 Provider、事件、Session 和 `run_task()`，只在实际出现重复装配时抽取共用函数，不预先建立通用服务或策略框架。

保留六个阶段用于记录进度，但把“先做真实任务，再完善体验”作为顺序。每阶段只实现必需能力，验证一条成功路径和关键失败路径；影响数据或权限的边界仍按 `AGENTS.md` 处理。

## 当前进度

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
