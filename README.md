# MediPet

MediPet 是面向单家门诊医院的智能就诊助手。当前仓库提供一个可运行的最小全栈切片：用户可通过聊天获得非诊断性的科室引导、演示号源、院内路线，并在明确确认后完成演示预约。

## 技术栈

- Web：Next.js、React、TypeScript、AI SDK UI、Tailwind CSS
- API：FastAPI、Pydantic、LangGraph
- 工具链：pnpm、uv、pytest、Ruff、Pyright、Vitest、Playwright

当前 Agent 使用确定性的内存演示数据，便于先验证端到端交互；尚未连接真实医院系统、数据库或大模型。

## 本地启动

需要 Node.js 22、Corepack、Python 3.12 和 uv。

启动 API：

```powershell
cd apps/api
uv sync
uv run uvicorn medipet.delivery.http:app --app-dir src --reload
```

另开终端启动 Web：

```powershell
cd apps/web
corepack pnpm install
corepack pnpm dev
```

打开 `http://localhost:3000`。API 健康检查位于 `http://localhost:8000/health`。

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

浏览器烟测脚本位于 `apps/web/tests/browser/smoke.py`，它会验证科室卡片、号源卡片和显式预约确认流程。
