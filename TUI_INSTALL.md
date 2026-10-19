# Lario TUI 安装、升级与回退

TUI 在用户电脑上读取、修改本地工作区；Web 账号与服务器不参与这条流程。
需要 Python 3.11+、uv 和模型 API Key；无需 Node.js 或前端构建。
命令验证功能另需本机 Docker Engine/Desktop（Linux 容器模式）和沙箱镜像。

## 普通用户：固定版本安装

项目尚未发布 PyPI 正式版本，不要运行 `uv tool install pyharness` 猜测包来源。
CI 的 Python 检查通过后会生成 `lario-tui-提交号` 构建产物，下载解压即可取得 wheel。
也可使用管理员提供的 wheel 安装（PowerShell / Linux 均适用）：

```sh
uv tool install --python 3.11 /path/to/pyharness-0.1.0-py3-none-any.whl
uv tool update-shell
```

重开终端，在目标项目目录启动。PowerShell 示例：

```powershell
$env:DEEPSEEK_API_KEY = '你的密钥'
cd C:\path\to\your-project
lario --model deepseek-chat --base-url https://api.deepseek.com/v1 --api-key-env DEEPSEEK_API_KEY --context-window 64000
```

确认工作区信任，然后描述任务。文件修改先审查 diff；命令先审查参数、目录和镜像。
默认工作区是启动目录；设置与会话在该目录 `.runtime/`，不在工具安装环境内。
不要把 `.runtime/`、密钥或用户资料推送到 Git。

也可固定 Git 提交安装，将 `COMMIT_SHA` 换成完整提交号：

```sh
uv tool install --python 3.11 "git+https://github.com/Dali0808/pyharness.git@COMMIT_SHA"
```

## 开发者：源码安装

```sh
uv tool install --editable /absolute/path/to/pyharness
```

这是跟随本地源码的开发安装；普通用户使用 wheel 或固定 Git 提交，方便复现和回退。
同一个工具环境不要同时维护开发版与正式版。`uv tool list` 查看当前来源。

## 升级与回退

1. 停止 TUI，备份目标项目的 `.runtime/` 和未提交的源码修改。
2. 保存旧 wheel/提交号，安装新的明确版本：

```sh
uv tool install --force --python 3.11 /path/to/new/pyharness-0.1.0-py3-none-any.whl
```

3. 在测试工作区运行 `lario --help`，再验证一条读取任务和一条经批准的修改。
4. 出现问题时，用同样的 `--force` 命令安装旧 wheel。应用回退不撤销工作区文件修改。

当前版本号仍是 0.1.0；本阶段按 wheel 的 SHA-256 或 Git 提交识别构建，不把同名 wheel 当作同一构建。
版本正式发布时应递增版本号。JSONL v2 与配置兼容规则保留；更换模型/提示词后需要新会话。
恢复旧会话时，不重放结果未知的工具调用。

## Docker 沙箱与卸载

在源码仓库按根 README 构建 `docker/sandbox.Dockerfile`，配置 `LARIO_SANDBOX_IMAGE`。
沙箱只执行批准的验证命令，容器内产物不回写；没有 Docker 时仍可读取和经批准编辑文件，
命令工具报错并拒绝回退到宿主机 shell。Web 服务器不需要也不挂这个 Docker 沙箱。

```sh
uv tool uninstall pyharness
```

卸载保留工作区与 `.runtime/`；是否删除历史由用户决定。

## 构建者核验

```sh
uv build --wheel
uv venv /path/to/clean-verification-venv --python 3.11
uv pip install --python /path/to/clean-verification-venv/bin/python dist/pyharness-0.1.0-py3-none-any.whl
/path/to/clean-verification-venv/bin/lario --help
```

Windows 将 `bin/python`、`bin/lario` 换成 `Scripts/python.exe`、`Scripts/lario.exe`。
wheel 包含 Python 入口，不打包网页静态资源；Web 使用源码构建镜像交付。
