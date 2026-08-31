# Docker Compose 一键开发启动

Type: spec
Status: resolved

## 背景

现有 `start-dev.ps1` 与 `start-dev.bat` 虽然统一编排了开发流程，但宿主机仍需分别具备 Node.js、Corepack、Python 和 uv，并由 PowerShell 脚本管理 API/Web 子进程。项目已经以 Docker Desktop 作为默认数据库依赖，因此将完整开发运行时纳入 Docker Compose，并用一个新的批处理入口完全替换现有宿主机启动入口。

当前模型已经配置为 DeepSeek，并通过既有 OpenAI-compatible 环境变量接入。本改动只负责把现有模型配置安全地传入 API 容器，不改变模型供应商、提示词或 Agent 行为。

## 目标

让 Windows 开发者在只安装并启动 Docker Desktop 的前提下，从仓库根目录使用一条标准命令启动 PostgreSQL、API 和 Web：

```powershell
docker compose up --build
```

面向日常使用的新入口是根目录 `start.bat`，可从资源管理器双击或从 CMD 运行。它直接执行上述 Compose 命令，不再调用 PowerShell，也不要求用户安装项目语言工具链。

## 启动合同

- 仓库根目录提供默认 `compose.yaml`，无需记忆项目名或额外 `--file` 参数。
- 删除现有 `start-dev.ps1` 与 `start-dev.bat`；根目录只保留新的标准入口 `start.bat`，避免并存入口产生歧义。
- `start.bat` 先切换到脚本所在的仓库根目录，检查 Docker CLI、Compose 插件和 Docker Engine 是否可用，然后以前台模式执行 `docker compose up --build`。
- Docker 不可用时，`start.bat` 输出可操作的中文错误并保留失败退出码；正常运行时，Compose 日志直接显示在当前窗口，不由批处理脚本二次转发或加工。
- Compose 分别运行 `postgres`、`api` 和 `web`，不把多个服务塞入同一个容器。
- API 在数据库健康后自动执行 Alembic migration 和幂等开发 seed，再启动 Uvicorn。
- Web 在 API 存活后启动，并保留开发模式热更新。
- PostgreSQL 使用命名卷持久化；普通停止或 `docker compose down` 不删除数据，只有显式 `down --volumes` 才删除。
- 浏览器访问地址保持为 `http://localhost:3000`，API 保持为 `http://localhost:8000`。
- Web 的服务端请求使用 Compose 内部地址 `http://api:8000`，浏览器端请求使用公开地址 `http://localhost:8000`；不得把容器内部主机名发送给浏览器。
- DeepSeek 继续使用 `apps/api/.env` 中现有的 `MEDIPET_LLM_PROVIDER=openai-compatible`、Base URL、模型名和 API Key。真实密钥只在容器运行时注入，不写入镜像层、Compose 文件、构建参数、日志或 Git 跟踪文件。
- API 保持 `MEDIPET_WEB_ORIGIN=http://localhost:3000`，数据库 URL 由 Compose 注入并指向 `postgres` 服务。
- API/Web 源码以 bind mount 提供热更新；容器内依赖使用独立卷或镜像层，不依赖宿主机 `.venv`、`node_modules`、Python、Node.js 或 uv。
- 服务具有可操作的 healthcheck 和依赖关系。启动失败时，`docker compose logs` 能直接定位到具体服务。

## 验收

- 在未安装宿主机 Node.js、Python、uv 或 pnpm 的等价干净环境中，仅依赖 Docker 即可构建和启动。
- `docker compose config` 成功，且解析后的配置不包含实际 DeepSeek API Key。
- 从无镜像、无应用容器的状态执行 `docker compose up --build` 后，PostgreSQL 健康、migration/seed 成功、`/health` 返回成功且 Web 可访问。
- `GET /ready` 能识别通过运行时环境传入的 DeepSeek 配置；自动化验收不得发送真实或可能计费的模型请求。
- 页面服务端可以恢复就诊事项和历史，浏览器端可以创建、切换就诊事项并发送请求，不出现 `localhost`/`api` 地址空间混用。
- 容器端 SSR 与浏览器端按同一医院时区格式化号源时间，不因容器使用 UTC 而出现 hydration mismatch。
- 修改 API 或 Web 源文件会触发对应开发服务重载，无需重建镜像。
- Ctrl+C 后应用容器停止且无宿主机 API/Web 残留进程；数据库数据在再次启动后仍存在。
- `start.bat` 可从资源管理器双击，也可从任意工作目录通过 CMD 调用，并始终启动仓库自身的默认 Compose 拓扑。
- Docker 缺失、Compose 插件缺失或 Docker Engine 未启动时，`start.bat` 不继续执行，并清楚说明需要安装或启动 Docker Desktop。
- `start-dev.ps1`、`start-dev.bat` 及其 README/架构引用均被移除，仓库中不再保留第二套宿主机启动入口。
- 原 `infra/compose.yaml` 的 PostgreSQL 定义合并进根目录 `compose.yaml`，旧文件及其引用被移除，避免维护两套 Compose 定义。
- README 将 `start.bat` 双击启动和 `docker compose up --build` 命令列为仅有的标准开发启动方式。
- 新 ADR 取代 ADR 0005 的“API/Web 默认在宿主机运行”决定，并同步 `docs/design/architecture.md`。

## 不在本次范围

- 更换 DeepSeek 模型、修改现有 API Key，或把模型密钥提交进仓库。
- 生产部署、HTTPS、域名、云端密钥管理或镜像仓库发布。
- 自动发送真实 DeepSeek 对话来验证可能计费的模型能力。
- 删除既有 PostgreSQL 数据卷。

## Answer

已由实现工单 01 完成：仓库现在以根目录 `compose.yaml` 编排 PostgreSQL、API 和 Web，并以 `start.bat` 作为唯一 Windows 日常入口。DeepSeek 配置通过运行时 Docker secret 注入；Web 服务端与浏览器端使用各自可达的 API 地址。完整构建、三服务健康启动、迁移与 seed、浏览器交互、热更新、非 root 运行和重启后数据持久化均已验证，未发送真实模型请求。
