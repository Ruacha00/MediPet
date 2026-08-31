# 01: 容器化 API/Web 并建立默认 Compose 启动入口

Type: implementation
Status: resolved

**What to build:** 为 API 和 Web 增加开发 Docker 镜像，在根目录 Compose 中编排 PostgreSQL、migration/seed、API 与 Web；删除现有 PowerShell/批处理启动器并新建直接启动 Docker 的 `start.bat`，使开发者无需宿主机语言工具链即可一键运行；安全复用现有 DeepSeek 配置，并保留热更新和持久化数据。

- [x] 为 `apps/api` 和 `apps/web` 添加可缓存依赖层、支持开发热更新且不以 root 运行应用的 Dockerfile。
- [x] 添加根目录 `compose.yaml`，包含 `postgres`、`api`、`web`、健康检查、依赖顺序、端口和持久化卷。
- [x] API 容器在 PostgreSQL 健康后执行 migration 与幂等 seed，成功后才启动服务。
- [x] 通过运行时 Docker secret 安全读取 `apps/api/.env` 中已有 DeepSeek OpenAI-compatible 配置；构建上下文、Compose 解析结果和日志不包含真实密钥。
- [x] 区分 Web 服务端的内部 API 地址与浏览器端公开 API 地址，覆盖历史恢复、能力状态、创建/切换事项和聊天传输。
- [x] 固定号源的医院时区展示，避免容器端 UTC 与浏览器端时区不同造成 hydration mismatch。
- [x] 使用源码 bind mount 与容器依赖卷支持 API/Web 热更新，不读取宿主机 `.venv` 或 `node_modules`。
- [x] 删除 `start-dev.ps1` 与 `start-dev.bat`，并清理所有文档、ADR 和脚本引用。
- [x] 新建根目录 `start.bat`：从任意当前目录均先定位仓库根目录，检查 Docker CLI、Compose 插件和 Engine，然后直接以前台模式执行 `docker compose up --build`。
- [x] `start.bat` 在 Docker 不可用时提供中文可操作错误并返回非零退出码；正常运行时保留原始 Compose 日志与 Ctrl+C 停止语义。
- [x] 将 `infra/compose.yaml` 的数据库定义合并进根目录 `compose.yaml`，删除旧文件与引用，避免双份配置。
- [x] 验证 `docker compose config`、全新构建、健康启动、迁移/seed、重启后数据保留及 API/Web 源码热更新。
- [x] 运行现有后端测试、前端测试、lint、类型检查、生产构建和不调用真实模型的浏览器烟测。
- [x] 验证从资源管理器双击 `start.bat`、在仓库根目录运行以及从其他工作目录调用三种入口行为。
- [x] 将 README 的启动说明收敛为 `start.bat` 和标准 Docker Compose 命令，不再描述宿主机启动流程。
- [x] 新增 ADR 取代 ADR 0005，并同步架构文档中的开发运行拓扑。

## Implementation notes

- 不要将 DeepSeek API Key 作为 Docker `ARG` 或硬编码进 Compose；Dockerfile 与 `.dockerignore` 不得复制 `apps/api/.env`。
- 浏览器不能访问 `http://api:8000`，Web 容器也不能用自身的 `localhost:8000` 访问 API。建议增加仅服务端读取的内部地址配置，同时保留客户端公开地址；也可采用同源代理，但必须用测试证明两侧均正确。
- 自动化验收只能检查 DeepSeek 配置是否被 API readiness 接受，不得发送可能计费的模型调用。
- `start.bat` 只负责编排 Docker，不在批处理里重新实现 migration、seed、健康等待或子进程管理；这些职责属于镜像入口与 Compose。

## Comments

- 2026-08-31：用户确认当前模型已使用 DeepSeek，并要求以 Docker 实现一键启动。
- 2026-08-31：用户要求替换现有 BAT/PowerShell 入口，改为新建一个直接启动 Docker 的 BAT；确定新标准入口为根目录 `start.bat`，不保留宿主机启动备用路径。

## Answer

已完成。根目录 `start.bat` 现在直接执行默认 `docker compose up --build`；Compose 使用三个独立服务、运行时 secret、命名数据库卷、健康依赖与源码 bind mount。API/Web 镜像以 UID 10001 运行，服务端使用 `http://api:8000`，浏览器保持 `http://localhost:8000`。

验收结果：`docker compose config` 与密钥防泄漏测试通过；实际镜像构建及三服务健康启动通过；`/health`、`/ready`、Web 首页和专用 Web health route 通过；浏览器烟测通过且未调用真实模型；重启前后 `visit_matters` 数量均为 1；后端 152 个测试通过、8 个跳过，Pyright/Ruff 通过；前端 17 个测试、ESLint 和生产构建通过。容器已正常停止但保留命名卷。
