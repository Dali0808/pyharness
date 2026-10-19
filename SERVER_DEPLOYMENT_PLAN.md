# Web 智能助手与本地 TUI 交付计划

更新：2026-10-20。基线：`062264e`。本轮代码与离线验证已完成，用户反馈本机测试成功；准备由用户提交，服务器尚未部署。

## 已确认的产品决策

- 一个 Agent 内核，两种工具装配：Web 只读用户资料并生成可下载产物；TUI 保留本地文件修改、审批和 Docker 命令工具。
- Web 管理员通过服务器命令创建账号，不开放注册；用户数据保留到主动删除。
- 可配置单文件、存储、每日模型请求与 token 限额，不接支付。
- 第一版上传 UTF-8 代码、文本、Markdown、JSON、CSV；不解包压缩文件，不解析 Office，不执行上传内容。
- Linux 单机单进程，SQLite 保存账号与用量，文件保存于持久卷；共用现有 JSONL 与任务循环，不引入队列或多 worker。
- 目标服务器已确认 OpenCloudOS Server 9、x86_64、2 核 2GB；域名、Docker/Compose 安装状态尚未确认。

## 职责、失败语义和验收

1. 拆分工具装配：后端不注册 Web 的 `write_file`、`edit_file`、`run_command`。恶意调用返回 unknown_tool，不产生副作用；TUI 原有审批与沙箱测试继续通过。
2. 用户资料：上传有界流式接收，拒绝越界、符号链接、二进制和同名覆盖；模型只读 documents，写入 artifacts；下载强制附件。不同用户的状态、Session 和文件互不可见；支持主动删除资料、产物和会话。
3. 登录与配额：密码哈希、有限期 HttpOnly cookie、同源校验、登出撤销与登录限速；用户不能更改服务端模型地址/密钥变量。模型调用在发出前持久化预留配额，覆盖摘要和子 Agent，取消/未知用量保守计费；不把 token 统计当作账单金额。
4. 交付：多阶段镜像、Compose/Caddy、健康与版本接口、CI、先构建再更新、空闲发布、数据库/文件一致备份及恢复/回滚说明。运行容器不挂 Docker socket。实际服务器发布需服务器与域名信息，当前仅交付可审查配置及本地验证。
5. TUI：独立安装/升级说明与 wheel 安装验证，保持 `lario` 入口与既有会话兼容。

## 验证方式

先运行受影响的 pytest，再全套；前端执行 TypeScript、生产构建与离线浏览器测试。模型使用 ScriptedProvider / MockTransport，不在自动测试调用收费模型。部署脚本做语法/配置检查；无 Docker/服务器时明确记录未验证项。

## 阶段记录

- 已核对 Git：基线已提交，仅用户已有的 `DOCKER_SANDBOX_PLAN.md` 未跟踪，本轮不修改该文件。
- 第 1 步完成：`toolsets.py` 共用装配，Web 只读 documents + create_artifact，关闭共享 MemoryCore；TUI 原工具顺序、审批与命令沙箱保留。
- 第 2 步完成：资料/产物上传、列表、下载、删除，独立 Session、两账号隔离；旧 Web Coding 会话不自动导入。
- 第 3 步完成：账号 CLI、scrypt 密码、撤销 Cookie、同源校验、限速与原子配额；预留覆盖摘要/重试，错误和取消保留预留，不接支付。
- 第 4 步代码完成：多阶段镜像、Compose/Caddy、健康/维护接口、CI 检查与 SHA 镜像、备份/恢复、空闲更新/失败回退脚本。768MB 应用 + 128MB 代理、默认 1 个任务适配小服务器。真实 Docker/CI/远程发布待环境验收。
- 第 5 步完成：独立 wheel 安装/固定提交安装、升级/回退文档，CI wheel 构建产物；已在干净 venv 从仓库外执行 `lario --help`。未发布 PyPI。
- Windows（Python 3.11.15）`uv run --locked pytest -q`：265 passed、13 skipped；跳过含 Windows 链接权限/Linux 脚本及显式真实 Docker。
- WSL Ubuntu 24.04（Python 3.12.3）同命令：277 passed、1 skipped（真实 Docker）；包括 5 个模拟维护脚本场景。
- Edge/Node 24.19.0：`cd frontend` 后 `npm run test:e2e`，7 passed（含 TypeScript 与生产构建），已检查桌面、移动端与资料面板截图。
- `uv build --wheel`、独立 venv 安装与入口检查通过；Compose config 与 `bash -n deploy/manage.sh` 通过。`git diff --check` 无空白错误。
- 原 `.venv` 同步遇到仍在运行的 `lario-web.exe` 占用，未终止用户旧服务；Python 验证改用 `.runtime/verification-venv`。更新时需用户在原终端停止旧服务，再同步重启。
- 未调用真实模型；Docker CLI 可用但引擎未启动，未构建/运行新镜像或触发 GitHub 工作流。部署步骤及恢复限制见 `deploy/README.md`。
- 2026-10-20：已停止占用项目启动器的旧 Web 服务，默认 `.venv` 同步与新入口检查成功；用户反馈本机测试成功。验收细项和模型参数未提供，单独记录用户反馈，不改变上述离线测试与部署验收范围。用户要求阶段提交后暂停开发，下次继续服务器验收；提交命令交由用户执行。
