# MediPet

MediPet 是面向单家门诊医院的智能就诊助手。当前仓库提供一个可运行的最小全栈切片：开发者可连接符合 OpenAI Chat Completions 契约的模型，就诊参与者可获得逐步流式、非诊断性的门诊就诊协助。

## 技术栈

- Web：Next.js、React、TypeScript、AI SDK UI、Tailwind CSS
- API：FastAPI、Pydantic、LangGraph
- 工具链：pnpm、uv、pytest、Ruff、Pyright、Vitest、Playwright

当前环境不包含医院科室、医生、号源、费用、预约或路线数据，也不会生成演示医院结果。医院数据和持久化将在后续能力接入时提供。

## 本地启动

需要 Node.js 22、Corepack、Python 3.12 和 uv。

启动 API：

```powershell
cd apps/api
uv sync
$env:MEDIPET_LLM_BASE_URL = 'https://api.example.com/v1'
$env:MEDIPET_LLM_API_KEY = 'replace-with-development-token'
$env:MEDIPET_LLM_MODEL = 'provider-model-name'
uv run uvicorn medipet.delivery.http:app --app-dir src --reload
```

`MEDIPET_LLM_BASE_URL` 是包含版本路径的完整 API 根地址；Adapter 会在其后请求 `chat/completions`。可选配置为 `MEDIPET_LLM_TEMPERATURE`（默认 `0`）和 `MEDIPET_LLM_TIMEOUT_SECONDS`（默认 `30`）。不要提交真实 Token。

另开终端启动 Web：

```powershell
cd apps/web
corepack pnpm install
corepack pnpm dev
```

打开 `http://localhost:3000`。API 存活检查位于 `http://localhost:8000/health`，模型配置就绪检查位于 `http://localhost:8000/ready`。缺少或无效模型配置时，存活检查仍成功，就绪检查和聊天请求返回 `503`。

## 验证

后端：

```powershell
cd apps/api
uv run pytest
uv run ruff check .
uv run pyright
```

前端：

```powershell
cd apps/web
corepack pnpm test
corepack pnpm lint
corepack pnpm build
```

浏览器烟测脚本位于 `apps/web/tests/browser/smoke.py`，它验证聊天外壳和零医院能力提示，不会调用真实或收费模型。
