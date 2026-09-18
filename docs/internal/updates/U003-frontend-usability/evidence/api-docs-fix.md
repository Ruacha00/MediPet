# API 文档入口修复（2026-09-18）

工作树：`D:/Projects/Agent Learn/Project/MediPet-Rebuild`，基于 `main` / `9cc9a53` 的未提交修复。

## 问题与修复

- 初查时 Docker 项目容器已停止、5174 开发预览未监听；浏览器原预览页显示连接拒绝。
- 前端链接为 `/api/python/docs`，Vite/Nginx 剥离 `/api/python` 后转发；原 Swagger HTML 请求根路径 `/openapi.json`，导致请求未经过 API 代理。原 schema 未配置 servers，试调也会落到前端根路径。
- 使用 FastAPI 官方 HTML helper 提供 `/docs` 和 `/redoc`，schema 地址为 `./openapi.json`，OpenAPI servers 为 `.`，使直连与代理都按 schema 所在地址访问 API。业务路由、确认逻辑和代理配置不变。
- README 与完整使用指南改为优先说明同源入口，保留 `8000/docs` 直连方式。

## 验证

- 修复前 ASGI 复现：HTML 内为 `url: '/openapi.json'`，代理路径的 schema 请求返回 404。
- `.venv/Scripts/python.exe -m pytest tests/test_api_docs.py tests/test_chat_api.py -q --tb=short`：24 passed（15.48 秒）。文档专项 4 项覆盖 Swagger/ReDoc × 直连/剥离前缀代理，并跟随 schema 解析 servers 后请求 health；此层用就绪状态替身，不调用模型或存储。
- `docker compose build api frontend` 成功，前端生产构建完成，API 镜像模型资产校验通过；`docker compose up -d --no-build --wait` 后 API、前端、Redis、Chroma 四服务 healthy，沿用原数据卷。
- 实际 HTTP：`8000/docs`、`5174/api/python/docs`、`8088/api/python/docs` 均返回 200 HTML；`8088/api/python/openapi.json` 返回 200 JSON。
- Edge 浏览器：从 `8088/` 点击“管理与调试 → API 文档”，新标签展示 MediPet Swagger UI 和接口列表；展开 GET /health → Try it out → Execute，页面显示请求 URL 为 `http://127.0.0.1:8088/api/python/health`，响应 200 且 `status: ok`。
- 未进行实际模型评测，未新增或确认预约；文档 UI 仍沿用 FastAPI helper 默认的外部静态资源。

## 影响与工作区

- GitNexus 显式绑定当前工作树，索引提交 `9cc9a53`。修改前 docsUrl/app 上游结果 UNKNOWN；结合 App.vue 的链接、Vite/Nginx 代理、FastAPI 文档注册及接口测试核实引用。
- 更新索引成功：4798 nodes、11510 edges、339 flows。索引本身存在跨语言与流程枚举边界，不能把缺失调用当作无影响。
- detect_changes 指向当前工作树，返回 14 个符号、无 partial/truncated 标记；其输出未涵盖全部中文文档及未跟踪测试，另以 Git 差异和专项测试核对。本轮不是全工作树提交验收。
- 索引工具自动追加的 AGENTS/CLAUDE 片段已恢复；新生成的 `.claude` 技能目录移至 ignored `.scratch/api-docs-gitnexus-generated-20260918`。此前文档措辞修改及未跟踪来源清单保留。
- 5174 Vite 开发预览保持运行；尚未提交或推送本次修复。
