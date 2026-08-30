# MediPet

MediPet 是面向单家门诊医院的智能就诊助手。当前仓库提供一个可运行的最小全栈切片：开发者可连接符合 OpenAI Chat Completions 契约的模型，就诊参与者可获得逐步流式、非诊断性的门诊就诊协助。

## 技术栈

- Web：Next.js、React、TypeScript、AI SDK UI、Tailwind CSS
- API：FastAPI、Pydantic、LangGraph
- 工具链：pnpm、uv、pytest、Ruff、Pyright、Vitest、Playwright

当前环境不包含医院科室、医生、号源、费用、预约或路线数据，也不会生成演示医院结果。PostgreSQL 只保存开发患者、就诊参与者、就诊事项和对话消息。

## 本地启动

需要 Node.js 22、Corepack、Python 3.12、uv 和 PostgreSQL。

启动 API：

```powershell
cd apps/api
uv sync
$env:MEDIPET_DATABASE_URL = 'postgresql://medipet:replace-with-development-password@localhost/medipet'
$env:MEDIPET_LLM_BASE_URL = 'https://api.example.com/v1'
$env:MEDIPET_LLM_API_KEY = 'replace-with-development-token'
$env:MEDIPET_LLM_MODEL = 'provider-model-name'
uv run alembic upgrade head
$env:PYTHONPATH = 'src'
uv run python -m medipet.persistence.seed
uv run uvicorn medipet.delivery.http:app --app-dir src --reload
```

`MEDIPET_LLM_BASE_URL` 是包含版本路径的完整 API 根地址；Adapter 会在其后请求 `chat/completions`。可选配置为 `MEDIPET_LLM_TEMPERATURE`（默认 `0`）和 `MEDIPET_LLM_TIMEOUT_SECONDS`（默认 `30`）。不要提交真实 Token。

`MEDIPET_DATABASE_URL` 必须是 PostgreSQL URL，修改后需要重启 API。Alembic 负责建表，应用启动时不会自动创建结构。开发 seed 可重复运行，只创建演示患者、参与者和就诊事项，不创建业务 Skill 或 Tool。

另开终端启动 Web：

```powershell
cd apps/web
corepack pnpm install
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

浏览器烟测脚本位于 `apps/web/tests/browser/smoke.py`，它验证聊天外壳和零医院能力提示，不会调用真实或收费模型。
