# Lario Web

简洁对话首页与三栏工作台，已接入 Python 的真实任务执行入口。
网页与 TUI 共用 `run_task()`、OpenAI-compatible Provider、工作区工具、
上下文压缩、只读子 Agent 和 JSONL Session。网页不再提供固定演示回复。

## 启动

在源码仓库使用 Python 3.11+、uv、Node.js 22.18+（推荐 24）和 npm。
先运行 `node --version`，确认至少为 22.18；Node 18 不满足当前依赖要求。
Windows 使用 nvm 时可安装并切换到 Node 24，再确认版本。
首次安装并构建：

```sh
uv sync --locked
cd frontend
npm ci
npm run build
cd ..
```

Windows 若在 `npm ci` 遇到 Rollup `.node` 文件的 `EPERM / unlink` 错误，
先在运行本项目 `npm run dev` / `npm run preview` 的终端按 Ctrl+C，
等进程退出后重新安装。仅刷新或关闭浏览器不会停止预览服务。
安装失败后先让 `npm ci` 成功，再运行构建；此时出现 `tsc` 找不到通常是依赖未装完整。

在**启动 Python 服务的同一个终端**设置密钥环境变量。例如 PowerShell：

```powershell
$env:DEEPSEEK_API_KEY = '你的密钥'
uv run --locked lario-web --workspace . --model deepseek-chat --base-url https://api.deepseek.com/v1 --api-key-env DEEPSEEK_API_KEY --context-window 64000 --max-steps 30
```

打开终端打印的 `http://127.0.0.1:8000/#token=…` 完整地址，确认工作区信任后即可使用。
`--workspace` 可指定其他项目目录；`--port 8001` 可更换端口。
后续启动也可以省略模型参数，从网页「项目设置」填写，或直接沿用 TUI 的 `.runtime/lario.json`。
环境变量必须在服务启动前设置；修改密钥后需重启服务并使用新连接地址。

前端更新后重新执行 `npm run build` 并刷新页面；后端更新后重启服务。
`npm run dev` / `npm run preview` 只提供静态界面，不能替代 Python 服务。
本版从源码目录的 `frontend/dist` 提供页面；安装发行包时不自动打包前端资源。

## 使用

1. 首页输入任务，点击发送或按 Enter 后进入工作台。Shift+Enter 换行，中文输入法确认候选词不会发送。
2. 回复通过 SSE 流式更新；Markdown 安全渲染，用户输入作为文本显示，外部图片不会自动加载。
3. 文件操作展示实际目标路径与 unified diff，批准后才写入。拒绝结果返回模型，由模型继续处理。
4. 命令展示完整参数、工作目录和 Docker 镜像，批准后使用 TUI 原有沙箱；结果显示实际退出码与有界输出。非零退出码和任务失败分别显示。
5. 「停止任务」取消模型请求或工具操作，撤销待处理审批。未完成流式文本标记为未保存，不会伪装成完整回答写入 Session。
6. 新建会话回到首页；侧栏支持搜索和切换。设置中的 Session 相对路径可继续已有 TUI 会话。网页与 TUI 不要同时写入同一个 Session。
7. 模型、Provider ID、API 地址、密钥环境变量名、步数、上下文窗口、只读子 Agent 开关和 Session 路径都可在设置中配置。留空上下文窗口关闭压缩；更改模型、上下文窗口或多 Agent 模式后需新建会话，原会话仍校验其元数据。
8. `/` 聚焦工作台会话搜索。审查标签支持左右方向键；设置弹窗支持 Escape。

设置和当前 Session 路径写入 `.runtime/lario.json`，完整消息写入原有 v2 JSONL。
网页新会话放在 `.runtime/web/sessions/`。刷新会重连当前状态；服务重启后恢复最近选择的会话，
未知结果的工具调用沿用原恢复规则，**不自动重放**。已保存对话与运行中的显示内容分离。
首页与工作台在同一个 URL 内切换，浏览器后退不用于两者切换。

## 本机边界

- 服务只监听 `127.0.0.1`，校验 Host、Origin 和每次启动生成的连接令牌。令牌进入浏览器当前标签的 sessionStorage，随后从地址栏移除；不要分享启动连接地址。
- API Key 只从 Python 进程的指定环境变量读取，配置文件和网页仅保存变量名与是否已设置的状态。
- 一个服务实例同一时间只运行一个任务；运行和等待审批时不能切换会话或修改设置。审批绑定当前运行与操作 ID，一次使用，停止后失效。
- 最后一个网页连接断开后有 20 秒重连时间，超时自动取消任务。断线重连恢复当前审批，不重复提交任务或执行工具。
- 工具继续使用原路径校验与大小限制。命令需要 Docker Desktop / Linux 容器和已构建镜像，见[根 README](../readme.md)；缺少 Docker 时返回工具错误，不回退到宿主机 shell。
- 界面只保留最近 200 条文本消息、100 条工具记录，每类最多约 256K 字符；单项最多约 32K 字符。完整 JSONL 不按界面窗口截短。Web 加载单个 Session 上限为 32 MB，历史列表最多自动发现 100 个 Web Session；更早会话可通过设置中的路径打开。
- SSE 每秒最多推送 20 次有界状态快照。服务仅用于本机单用户，不提供公网部署或多用户隔离。

## 验证

仓库根目录：

```sh
uv run --locked pytest -q test/test_web.py test/test_cli.py test/test_tui.py test/test_session.py test/test_command.py test/test_coordination.py test/test_builtins.py
uv run --locked pytest -q
```

前端目录：

```sh
npm test
npm run test:e2e
```

`npm test` 执行 TypeScript 检查。浏览器测试先构建前端，再由 uv 自动启动
`test.web_browser_server`，通过真实 HTTP/SSE、应用入口和文件工具完成闭环。
该测试专用服务使用临时工作区、ScriptedProvider 和 DockerStub，**不调用收费模型或真实 Docker**，
不会改动当前项目文件。字体请求本地替换，测试不依赖 Google Fonts。
若未安装 Playwright Chromium，执行 `npx playwright install chromium`，或使用本机 Edge：

```powershell
$env:LARIO_BROWSER_CHANNEL = 'msedge'
npm run test:e2e
```

`test/test_web.py` 另使用 `httpx.MockTransport` 验证真实 OpenAI-compatible 流式适配器，
覆盖认证、信任、一次性审批、文件写入、命令输出、取消、会话恢复、兼容性、子 Agent 和设置保密。
离线测试结果不代表真实模型编码成功率；真实模型和容器验收须单独记录环境与结果。

## 文件职责

- `coding_agent/web.py`：本机 HTTP/SSE、连接与审批生命周期、界面状态映射；通过 `run_task()` 执行任务。
- `coding_agent/settings.py`：从 TUI 提取的共用设置与参数解析，维持原配置格式。
- `src/api.ts`：认证请求、SSE 解析和重连；只自动重连读取状态，不重试写操作。
- `src/App.tsx`：首页、任务对话、工具记录、真实审批与设置。
- `src/styles.css`：近黑背景、白色胶囊按钮、黄至青绿的输入框底光、响应布局与减少动画偏好。

布局参考：[CloudCLI](https://github.com/siteboon/claudecodeui)、[OpenCode Web](https://opencode.ai/docs/web/)。
