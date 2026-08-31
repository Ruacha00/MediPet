# MediPet

MediPet 是面向单家门诊医院的智能就诊助手。当前仓库提供一个可运行的最小全栈切片：开发者可连接符合 OpenAI Chat Completions 契约的模型，就诊参与者可获得逐步流式、非诊断性的门诊就诊协助。

## 技术栈

- Web：Next.js、React、TypeScript、AI SDK UI、Tailwind CSS
- API：FastAPI、Pydantic、LangGraph
- 工具链：pnpm、uv、pytest、Ruff、Pyright、Vitest、Playwright

开发环境包含独立且明确标识为虚构的医院目录，并提供“医院服务目录查询”“医院预约挂号协助”和“医院预约取消协助”三个首方 Skills。它们支持医院、科室、医生、号源和当前患者预约查询，以及明确确认后的创建或取消预约。服务医院事实只来自各 Skill 显式绑定的 Tool；产品不提供症状到科室的自动导诊规则，当前也不提供院内路线。

当前消息包含有限、明显的急症信号时，MediPet 会在调用模型前中断普通协助，直接显示拨打 120、前往急诊或请身边人协助的线下干预引导。该功能不是急症诊断，也不创建人工审核队列、消息推送或后台处置流程。

## 本地启动

本地开发只需要安装并启动 Docker Desktop。先确认 `apps/api/.env` 中已经配置 DeepSeek 的 OpenAI-compatible Base URL、API Key 和模型名；该文件只会作为运行时 Docker secret 挂载，不会复制进镜像或提交到 Git。

Windows 可以直接双击仓库根目录的 `start.bat`，也可以从 CMD 运行：

```bat
start.bat
```

批处理入口会检查 Docker CLI、Compose 插件和 Docker Engine，然后直接执行标准 Compose 命令。也可在仓库根目录手动运行同一命令：

```powershell
docker compose up --build
```

Compose 会构建并启动独立的 PostgreSQL、API 和 Web 容器，在数据库健康后自动应用 Alembic migration 和幂等虚构开发 seed。API 与 Web 源码通过 bind mount 保留热更新，依赖安装在镜像层中，因此宿主机不需要 Node.js、Python、pnpm 或 uv。

首次启动需要下载基础镜像并安装锁定依赖，之后会复用本地镜像层。Web 镜像默认从 `https://registry.npmmirror.com` 下载经过 lockfile 完整性校验的 npm 包；如需改用官方源，可在启动前设置 `PNPM_REGISTRY=https://registry.npmjs.org`。服务就绪后打开 `http://localhost:3000`；开发环境的 Skill / Tool 能力治理台位于 `http://localhost:3000/admin/capabilities`；API 存活检查位于 `http://localhost:8000/health`，模型和数据库就绪检查位于 `http://localhost:8000/ready`。

治理台及其 Web 管理 handlers 仅在非生产环境注册。浏览器不会接触管理 Token；Next.js 服务端使用与 API 一致的 `MEDIPET_MANAGEMENT_TOKEN` 代为调用现有管理 API。Compose 提供仅供本地开发的默认值，也可以在启动前设置同名环境变量覆盖。当前单人开发版本通过集中授权接缝放行固定的 `development-admin`，未提供登录或角色门禁；未来身份系统应接入该接缝。

Compose 启动并健康后，可用 `python apps/web/tests/browser/capability_admin_smoke.py` 验证新建、绑定、审核、发布、Tool 启停、审计与刷新恢复的完整浏览器闭环；该烟测只使用本地 Registry 与假医院 Tool，不调用模型。

按 `Ctrl+C` 会停止整个前台 Compose 会话。PostgreSQL 数据保存在命名卷中，普通停止和 `docker compose down` 都不会删除；只有明确执行以下命令才会连同开发数据一起删除：

```powershell
docker compose down --volumes
```

模型配置示例：

```dotenv
MEDIPET_LLM_PROVIDER=openai-compatible
MEDIPET_LLM_BASE_URL=https://api.deepseek.com
MEDIPET_LLM_API_KEY=replace-with-development-token
MEDIPET_LLM_MODEL=deepseek-chat
MEDIPET_LLM_TEMPERATURE=0
MEDIPET_LLM_TIMEOUT_SECONDS=30
MEDIPET_TURN_TIMEOUT_SECONDS=60
MEDIPET_AGENT_MAX_STEPS=8
MEDIPET_CONTEXT_MESSAGE_LIMIT=20
```

`MEDIPET_LLM_BASE_URL` 是完整 API 根地址；Adapter 会在其后请求 `chat/completions`。可复制 `apps/api/.env.example` 作为起点，但不要提交真实 Token、数据库 URL 或任何真实患者/医院数据。Web 可以为同一就诊参与者创建和切换彼此隔离的就诊事项，并恢复各事项历史；文本消息支持 GitHub Flavored Markdown，输入区提供常用格式按钮与发送前预览。

## 外置 Skills 与 Tools

可变能力定义统一放在仓库根目录的 `capabilities/`，不再放进 Python `src/`：

- `capabilities/skills/` 保存 `SKILL.md`、治理元数据和各 Skill 的显式 Tool 绑定；
- `capabilities/tools/hospital.json` 保存 Tool 名称、描述、输入输出 Schema、版本与审批属性；
- `capabilities/tools/fake-hospital.json` 保存仅用于开发环境的虚构医院数据。

Compose 会把该目录以只读方式挂载到 API 容器的 `/app/capabilities`，并通过
`MEDIPET_CAPABILITIES_PATH` 指定位置。空 Skill/Tool Registry 首次启动时会自动启用并发布
仓库自带的开发能力；修改定义后重启 API 服务会重新执行定义同步：

```powershell
docker compose restart api
```

后续同步不会覆盖管理端保存的启停、发布、退休或活动版本状态。新的 Tool 契约版本默认停用，
新增或发生内容、绑定变化的 Skill 版本保持 `draft`，需要通过开发管理 API 显式启用、审核和
发布。这样普通重启不会撤销管理员的停用或回滚决定。

Tool 的 Python 执行器仍属于受信任代码。外部清单只能配置已部署的执行器，不能通过 JSON
挂载任意代码；修改已有 Tool 契约时需要同步提升版本号。

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
