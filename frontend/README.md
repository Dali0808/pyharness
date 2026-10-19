# Lario Web 智能助手

Web 用于代码阅读与审查、项目文档和文本文件处理；本地修改、补丁审批与命令验证使用 TUI。
两端复用 `run_task()`、Provider、流式事件、预算与 JSONL；Web 只注册资料读取与产物创建工具。
首页保持单独对话框，发送后进入工作台；近黑背景、白色胶囊按钮和渐变输入框沿用既有风格。

## 用户操作

1. 访问管理员给出的 HTTPS 网站，用管理员创建的账号登录。
2. 点击「资料与产物」上传 UTF-8 代码、文本、Markdown、JSON 或 CSV。
   第一版不接受压缩包、PDF、Office、图片或二进制；资料采用平铺文件名，不保留目录结构。
3. 关闭文件窗口，在首页描述任务，例如“阅读 main.py，生成一份 Markdown 审查报告”。
   Enter 发送，Shift+Enter 换行，中文输入法确认候选不发送。
4. 工作台展示流式回答、工具结果和失败原因；需要下载时要求助手创建产物，
   然后在「资料与产物」的产物列表点击下载。
5. 可停止任务、新建或切换会话；删除文件和会话需确认。运行中不能上传、删除或切换会话。
6. 在资料面板查看存储和每日请求/token 用量；“请求次数”包括一条任务中的多次模型调用。
7. 退出登录；刷新/重启保留已保存会话。所有页面断开超过 20 秒会停止任务。

上传与产物同名文件不会覆盖，请换名字或先主动删除旧文件。
删除文件后，历史引用仍可能留在会话中；需要同时删除相关会话。管理员备份独立保留。
停止时的部分回答标记为未保存，不作为完整模型消息写入 Session。
网页不提供模型地址/密钥设置，联系管理员修改。完整用量及数据边界见[部署文档](../deploy/README.md)。

## 开发者本机预览

源码需要 Python 3.11+、uv、Node.js 22.18+（推荐 24）和 npm。
Node 18 不满足当前依赖；先检查 `node --version`。

```sh
uv sync --locked
cd frontend
npm ci
npm run build
cd ..
```

在启动后端的同一个 PowerShell 设置密钥：

```powershell
$env:DEEPSEEK_API_KEY = '你的密钥'
uv run --locked lario-web --workspace . --model deepseek-chat --base-url https://api.deepseek.com/v1 --api-key-env DEEPSEEK_API_KEY --context-window 64000 --max-steps 20
```

打开终端打印的 `http://127.0.0.1:8000/#token=…` 完整地址，确认开始使用资料空间。
连接令牌进入当前标签 sessionStorage 后从地址栏移除，不要分享。
`--workspace` 只指定存储基目录；只读上传的 documents，不会读取这个项目源码。
管理文件在 `.runtime/assistant/`，与 TUI 的设置、会话分离；旧 Coding Web 会话不自动导入。
这个命令仅监听本机、不提供账号或服务器配额；公网多用户使用 `lario-server` 与 Compose。

本机也可预览账号模式，在同一个终端设置：

```powershell
$env:LARIO_MODEL = 'deepseek-chat'
$env:LARIO_PUBLIC_URL = 'http://127.0.0.1:8000'
uv run --locked lario-server create-user alice
uv run --locked lario-server serve
```

打开 `http://127.0.0.1:8000/` 登录。默认数据目录 `.runtime/server`，两种本机模式不能占用同一端口。
密钥修改要重启后端；前端修改后重新构建。`npm run dev`/`preview` 不能替代 Python API 服务。
从原版本升级：在原终端 Ctrl+C 停止旧 Python 服务，再同步和重启，避免新页面连接旧 API。

Windows `npm ci` 若报 Rollup `.node` 的 EPERM/unlink，先停止本项目的 Vite 开发/预览进程，
再重新安装。安装未完成时不要继续构建，`tsc` 找不到通常是依赖未装完整。
若 `uv sync` 提示 `lario-web.exe` 被占用，也先在原服务终端停止它，不必删除虚拟环境。

## 验证

仓库根目录：

```sh
uv run --locked pytest -q test/test_web.py test/test_server.py test/test_ops.py
uv run --locked pytest -q
```

前端目录：

```sh
npm test
npx playwright install chromium
npm run test:e2e
```

Windows 可用已安装的 Edge：`$env:LARIO_BROWSER_CHANNEL = 'msedge'`。
浏览器测试通过临时账号、HTTP/SSE、真实文件工具和 `ScriptedProvider`，不调用模型或 Docker。
覆盖登录/登出、上传/读取/下载/删除、用户隔离、禁止编码工具、移动端、IME、停止和主题。
`test/test_web.py` 用 MockTransport 另验证 OpenAI-compatible 流式协议；离线通过不代表真实模型成功率。

## 文件职责和限制

- `coding_agent/web.py`：共用助手 HTTP/SSE、状态与本机令牌模式。
- `coding_agent/server.py`、`accounts.py`：账号模式、用户装配与配额。
- `coding_agent/files.py`、`toolsets.py`：上传/产物边界与工具装配。
- `src/api.ts`：Cookie/令牌请求、SSE 重连；不自动重试写请求。
- `src/App.tsx`：登录、首页、工作台和资料面板；不实现 Agent 决策。

单项展示约 32K 字符，当前会话最多 200 条文本/100 条工具、每类约 256K 字符。
显示截短不修改完整历史；超出模型输出限制仍会明确失败，可要求分章节回答或生成多个产物。
服务端文件限额、Session 限制和 UTC 配额见部署文档。自定义原始 HTML 和外部 Markdown 图片不执行/加载。
界面字体请求 Google Fonts，失败时回退系统字体；离线测试使用回退字体。
