# Web 智能助手部署与维护

目标：OpenCloudOS Server 9、x86_64、2 核 2GB。采用单进程应用 + SQLite + 文件卷 + Caddy HTTPS。
镜像在 CI 构建，服务器拉取；不在 2GB 服务器运行 npm/镜像构建或本地大模型。
默认一次运行 1 个任务，应用 768MB、Caddy 128MB；这只是起始配置，实际容量仍需压测。

## 上线前提

- 服务器安装 Docker Engine 与 Compose v2+ 插件，`docker info` 和 `docker compose version` 正常。
  使用 OpenCloudOS 支持的安装方式核对发行版兼容性；本文不自动执行未知来源安装脚本。
- 域名 A 记录指向服务器；若配置 AAAA 也必须可达。云安全组与主机防火墙开放 TCP 80/443，
  SSH 仅按管理需要开放。不要开放应用 8000 端口。国内域名与服务器的备案要求另行确认。
- 当前尚未确认域名和 Docker 安装情况；无域名时先用源码方式在本机验证，不把 HTTP 登录暴露公网。
- 保留磁盘空间供用户资料、会话和备份；用户不公开注册，只接收管理员创建的账号。

## CI 和镜像

`.github/workflows/ci.yml` 在 PR 上跑 Python、浏览器和镜像启动检查；合入 main/master 且检查通过后，
发布 `ghcr.io/dali0808/pyharness:sha-完整提交号`。只构建 linux/amd64，符合当前服务器架构。
镜像启动检查只访问页面与健康接口，不调用模型。工作流尚需推送到 GitHub 后实际运行。

GitHub 仓库需启用 Actions，并允许 GITHUB_TOKEN 写 Packages；私有包需服务器先 `docker login ghcr.io`。
登录令牌只在服务器交互输入，按所需范围授予 read:packages。不要写入仓库或构建参数。
Python/Node/uv 锁定到文件指定版本；基础镜像标签仍需定期检查安全更新，严格复现可另固定 digest。

## 首次启动

把仓库 `deploy/` 文件复制到服务器的固定目录，例如 `/opt/lario/deploy/`。
后续均在这个目录操作，不同版本不要随意改变 Compose 项目名和数据卷名。

```sh
cd /opt/lario/deploy
cp .env.example .env
chmod 600 .env
```

用编辑器填写 `.env`：`LARIO_IMAGE` 为 CI 已产出的完整 SHA 标签，
`LARIO_DOMAIN=你的域名`、`LARIO_PUBLIC_URL=https://你的域名` 必须一致；
填写模型名称、API 地址与密钥。默认读取 `DEEPSEEK_API_KEY`；若修改 `LARIO_API_KEY_ENV`，
需同时在 `.env` 中添加同名变量。不将密钥放在命令参数、网页或镜像内。

```sh
docker compose --env-file .env config --quiet
docker compose --env-file .env pull
docker compose --env-file .env up -d
docker compose ps
docker compose exec web lario-server create-user alice
```

创建账号交互输入两次至少 12 字符密码。用户名为 3–64 位 ASCII 字母、数字、点、下划线或横线。
访问 `https://你的域名` 登录，上传 UTF-8 资料，发送任务，下载产物。
首次证书签发失败先排查 DNS、80/443 和服务器时间。日志：`docker compose logs --tail 100 web caddy`。
日志与健康输出也应私密保管；不启用记录用户请求内容的代理 access log。

```sh
docker compose exec web lario-server reset-password alice
docker compose exec web lario-server disable-user alice
```

密码重置/禁用会撤销登录；SSE 下次事件或心跳检测到撤销后断开，无其他连接的任务按 20 秒宽限停止。
禁用不删除数据。用户可在网页删除自己的资料、产物和会话；模型用量记录供管理员保留。

## 数据与额度

`lario_data` 卷内 `/data/accounts.sqlite3` 保存账号、密码哈希、登录令牌哈希、限速和 UTC 每日用量。
每用户数据位于 `/data/users/<随机ID>/.runtime/assistant/` 的 documents、artifacts、sessions。
模型只读 documents，通过专用工具新建 artifacts；不能访问服务器源码、密钥、其他用户目录或执行命令。
应用容器不包含 Docker CLI，不挂 Docker socket。容器隔离不等于可开放不可信任脚本；本版不提供执行能力。

默认每用户单文件 1,000,000 字节、资料与产物合计 50MB/200 个文件；单文件配置不能高于 1MB。
最多 100 个会话；会话历史另有 32MB 总量准入限制，运行前检查，可超出最后一次运行的增量；
它不包含在 50MB 文件额度中。应监测整个数据卷磁盘，暂不提供全站磁盘硬配额。
若要降低配置额度，应先清理超限资料，避免现有文件数量超过新上限。

每日默认 200 次模型调用、1,000,000 tokens，UTC 零点按新日期重新计数。
“次数”包括工具循环、重试和摘要，不是用户消息数；每次实际发往模型前原子预留额度。
成功且有 usage 时按返回值结算；失败、取消、无 usage 时保留估算额度。
估算采用 UTF-8 字节数加输出预留与余量，不是供应商账单或绝对费用封顶；实际 usage 可能超过估算，
供应商侧还应配置账户预算。无支付、无自动续费。

登录 cookie 有效 12 小时、HttpOnly/Secure/SameSite=Strict；写 API 要求同源 Origin。
单账号每 10 分钟最多 10 次登录尝试，来源地址最多 100 次；不信任 forwarded headers，
通过 Caddy 的访问共享这个来源上限。这是小规模初版的限制，扩大用户量时再配置可信代理地址。
应用最多缓存 8 个用户空间，连接上限 128，不支持多 worker 或多副本共享数据卷。
删除上传文件不自动删除历史引用；需要同时删除含有该内容的会话和管理侧备份才算完整清理。

## 日常更新与代码回退

先由 CI 构建并确认目标镜像存在，再在服务器运行：

```sh
bash manage.sh update ghcr.io/dali0808/pyharness:sha-完整40位提交号
```

脚本有互斥锁，先拉镜像，再拒绝新任务，等已有任务结束（最多 5 分钟，超时取消维护而不杀任务）。
空闲后停止 Web，备份 SQLite 和文件，再启动新镜像并检查健康；检查失败会重启旧镜像。
成功后把镜像写入忽略于 Git 的 `release.env`。有短暂维护窗口，不承诺无停机更新。
期间不要运行另一个 Compose 更新或账号管理命令。

后续手工 Compose 命令必须带上两个 env 文件，避免意外恢复 `.env` 中的旧镜像：

```sh
docker compose --env-file .env --env-file release.env ps
docker compose --env-file .env --env-file release.env logs --tail 100 web
```

代码回退同样用 `manage.sh update 旧SHA镜像`，仍先备份。
当前数据库格式为 1；后续迁移必须先说明向后兼容性。代码回退不撤销用户新文件，
不兼容的数据迁移应恢复旧版匹配的备份，而不是强行启动旧代码。
维护中进程被 SIGKILL/断电时可能留下 maintenance.flag；查清任务/备份状态后手工解除：

```sh
docker compose --env-file .env --env-file release.env run --rm --no-deps --entrypoint python web -c "from pathlib import Path; Path('/data/maintenance.flag').unlink(missing_ok=True)"
```

## 备份、异地保管与恢复

```sh
bash manage.sh backup
```

脚本同样等待空闲、停止、备份、重新启动。输出备份路径，位于 `lario_backups` 持久卷。
备份有清单与逐文件 SHA-256、数据库完整性校验；禁止链接与越界恢复，不覆盖已有文件。
备份包含密码哈希及用户资料，**未加密**；仅管理员访问，尽快加密后复制到另一台机器/对象存储。
同服务器的另一个卷只能防误操作，不能防整机丢失。当前不自动删除备份；管理员制定保留周期并监测空间。

将脚本输出的文件名替换到下面命令，将备份取到当前目录（不用暴露宿主机卷路径）：

```sh
docker compose --env-file .env --env-file release.env cp web:/backups/lario-实际时间.tar.gz ./lario-backup.tar.gz
chmod 600 lario-backup.tar.gz
```

首次更新前没有 release.env 时省去第二个 `--env-file release.env`。
恢复必须使用空的新卷，保留当前数据卷供回退；先停止 Web，不与备份/管理进程并发。
以下用占位符 `MATCHING_IMAGE` 与实际备份文件名替换；备份清单中的 release 可确定匹配镜像：

```sh
docker compose --env-file .env --env-file release.env stop web
docker volume create lario_restore_20261019
docker run --rm --network none --read-only --tmpfs /tmp --user 10001:10001 -v lario_backups:/backups:ro -v lario_restore_20261019:/data --entrypoint python MATCHING_IMAGE -m coding_agent.ops restore /backups/lario-实际时间.tar.gz /data
```

恢复失败的新卷保留供排查，修复后换另一个空卷重试；绝不删除原卷。
成功后在 `.env` 增加 `LARIO_DATA_VOLUME=lario_restore_20261019`，
并将 `release.env` 中镜像改为匹配版本，再 `docker compose --env-file .env --env-file release.env up -d`。
数据始终挂在 `/data`，以保留 Session 工作区校验。恢复会清除备份中的登录令牌，所有用户重新登录。
恢复时点之后的文件/用量不会合并。`.env` 与 TLS 证书不在应用数据备份中，应分别保管。

## 当前验收边界

离线测试覆盖隔离、越界、额度并发、备份完整性，以及模拟 Docker 的更新成功/备份失败/版本失败回退。
本机 Docker 引擎尚未运行，未完成真实镜像构建和容器运行；CI 配置已提供，须以首次 Actions 成功结果为准。
尚未在这台 OpenCloudOS 服务器执行部署，也未验证真实域名 HTTPS 或收费模型；上线前仍需上述环境验收。
