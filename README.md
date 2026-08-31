# MediPet

MediPet 是面向单家门诊医院的智能就诊助手。当前仓库提供一个可运行的最小全栈切片：开发者可连接符合 OpenAI Chat Completions 契约的模型，就诊参与者可获得逐步流式、非诊断性的门诊就诊协助。

## 技术栈

- Web：Next.js、React、TypeScript、AI SDK UI、Tailwind CSS
- API：FastAPI、Pydantic、LangGraph
- 工具链：pnpm、uv、pytest、Ruff、Pyright、Vitest、Playwright

开发环境包含独立且明确标识为虚构的医院目录，并通过首方“医院预约挂号协助”Skill 提供医院、科室、医生、号源和当前患者预约查询，以及明确确认后的创建预约能力。服务医院事实只来自已绑定 Tool；当前仍不提供院内路线或症状到科室的医院导诊规则。

## 本地启动

需要 Node.js 22、Corepack、Python 3.12 和 uv。默认数据库由 Docker Desktop 通过 Compose 提供；如果已经有可用的 PostgreSQL，也可以直接传入其 URL 而不使用 Docker。

Windows 开发环境从仓库根目录用一个命令启动。脚本会准备持久化的开发 PostgreSQL、按 lockfile 增量同步依赖、应用迁移、执行幂等的虚构开发 seed，再等待 API 与 Web 实际可访问并持续显示带来源前缀的日志：

```powershell
.\start-dev.ps1
```

首次运行可能需要拉取 `postgres:17-alpine`。数据库容器和命名卷在 API/Web 退出后默认保留，因此后续启动不会丢失开发数据；需要退出时同时停止数据库可运行 `.\start-dev.ps1 -StopDatabaseOnExit`。应用已经退出后，也可运行 `docker compose --project-name medipet-dev --file infra/compose.yaml stop postgres` 单独停止数据库。这两种方式都不会删除数据卷。

已有 PostgreSQL 时，显式提供 URL 即可跳过 Docker：

```powershell
.\start-dev.ps1 -DatabaseUrl 'postgresql://medipet:replace-with-development-password@localhost/medipet'
```

也可以从 CMD 运行批处理入口；所有参数会原样传给 PowerShell 脚本：

```bat
start-dev.bat
```

模型配置来自 `apps/api/.env` 或进程环境。缺少或无效模型配置时，API 和 Web 仍会启动，但脚本会警告 `/ready` 与聊天暂不可用；修正模型配置后 development 环境会自动恢复。可使用 `-ApiPort`、`-WebPort` 和 `-DatabasePort` 调整端口，`-StartupTimeoutSeconds` 调整默认 60 秒的等待时间，`-OpenBrowser` 在服务存活后打开 Web，`-SkipMigrations` 或 `-SkipSeed` 跳过相应准备步骤。跳过 seed 只保留数据库现有的数据与能力状态，不会隐式停用既有 Skill 或 Tool。按 `Ctrl+C` 或任一应用异常退出时会同时停止 API 与 Web。

也可以分别启动两个服务：

启动 API：

```powershell
cd apps/api
uv sync --frozen
$env:MEDIPET_ENVIRONMENT = 'development'
$env:MEDIPET_DATABASE_URL = 'postgresql://medipet:replace-with-development-password@localhost/medipet'
uv run alembic upgrade head
$env:PYTHONPATH = 'src'
uv run python -m medipet.persistence.seed
uv run uvicorn medipet.delivery.http:app --app-dir src --reload
```

在 `apps/api/.env` 中放置可热加载的模型运行配置：

```dotenv
MEDIPET_LLM_PROVIDER=openai-compatible
MEDIPET_LLM_BASE_URL=https://api.example.com/v1
MEDIPET_LLM_API_KEY=replace-with-development-token
MEDIPET_LLM_MODEL=provider-model-name
MEDIPET_LLM_TEMPERATURE=0
MEDIPET_LLM_TIMEOUT_SECONDS=30
MEDIPET_TURN_TIMEOUT_SECONDS=60
MEDIPET_AGENT_MAX_STEPS=8
MEDIPET_CONTEXT_MESSAGE_LIMIT=20
```

`MEDIPET_LLM_BASE_URL` 是包含版本路径的完整 API 根地址；Adapter 会在其后请求 `chat/completions`。可复制 `apps/api/.env.example` 作为本地起点，但不要提交真实 Token、数据库 URL 或任何真实患者/医院数据。

development 环境会在 `/ready` 检查和每个新 turn 边界重新读取 `.env`。每个 turn 固定自己的配置快照，文件修改只影响后续 turn；无效的新配置会让 readiness 和新 turn 返回 `503`，修正文件后自动恢复。进程环境变量优先于 `.env`，因此通过 PowerShell 或部署环境注入的同名字段不会被文件覆盖。配置变化日志只包含版本指纹、来源和字段名。

test 应通过依赖注入提供模型与配置；production 和 test 运行环境只使用进程启动时的静态配置，不监听 `.env`。`MEDIPET_DATABASE_URL`、`MEDIPET_ENVIRONMENT` 和 `MEDIPET_MANAGEMENT_TOKEN` 属于启动配置，修改后需要重启 API。

`MEDIPET_DATABASE_URL` 必须是 PostgreSQL URL，修改后需要重启 API。Alembic 负责建表，应用启动时不会自动创建结构。开发 seed 可重复运行：它创建虚构患者、参与者和就诊事项，同步并启用七个医院 Tool，并创建、绑定和发布仓库内的医院预约 Skill。生产部署不运行该 seed，也不会自动激活虚构医院能力。

另开终端启动 Web：

```powershell
cd apps/web
corepack pnpm install --frozen-lockfile
corepack pnpm dev
```

打开 `http://localhost:3000`。API 存活检查位于 `http://localhost:8000/health`，模型和数据库就绪检查位于 `http://localhost:8000/ready`。缺少或无效模型/数据库配置时，存活检查仍成功，就绪检查和相应请求返回 `503`。Web 会从 `GET /v1/visit-matters/{visit_matter_id}/messages` 恢复刷新前的历史，并保留失败、取消和未完成状态。

## 验证

后端：

```powershell
cd apps/api
uv run pytest
uv run ruff check .
uv run pyright
```

PostgreSQL Store 契约测试需要指向专用一次性测试库：

```powershell
$env:MEDIPET_TEST_DATABASE_URL = 'postgresql://medipet:test-password@localhost/medipet_test'
uv run pytest tests/test_postgres_conversation_store.py
```

契约测试只执行幂等的 `alembic upgrade head`，不会自动降级或清空传入的数据库。

前端：

```powershell
cd apps/web
corepack pnpm test
corepack pnpm lint
corepack pnpm build
```

浏览器烟测脚本位于 `apps/web/tests/browser/smoke.py`，用于验证聊天外壳；自动测试不会调用真实或收费模型。

## 可观测性与基准

每个 turn 会记录去标识化运行指标：首 token 延迟、总耗时、模型耗时、Tool 耗时、模型请求次数、输入/输出 token、Agent 步数和终止结果。指标只按 `provider`、`model` 和 Runtime Profile 版本区分，不包含参与者原文、完整 Prompt、Token、数据库 URL、上游错误正文或就诊事项标识。开发环境维护者可通过带管理 Bearer Token 的 `GET /v1/admin/run-metrics` 查询。

fake benchmark 使用固定模型响应、真实应用/SSE/Runtime 路径和专用 PostgreSQL 测试库，不访问外部模型：

```powershell
cd apps/api
$env:MEDIPET_BENCHMARK_DATABASE_URL = 'postgresql://medipet:test-password@localhost/medipet_benchmark'
$env:PYTHONPATH = 'src'
uv run python -m medipet.benchmark fake --iterations 10
```

live benchmark 必须额外显式启用，并使用与正常运行相同的 `MEDIPET_LLM_PROVIDER`、模型 URL、Token 和模型名配置：

```powershell
$env:MEDIPET_ENABLE_LIVE_BENCHMARK = '1'
uv run python -m medipet.benchmark live --iterations 3
```

两种命令都只发送内置虚构文本，输出按 provider/model/profile 区分的去标识化 JSON 报告。CI 只运行 fake benchmark，不配置或访问真实、收费模型，也不采用跨供应商绝对延迟门槛。
