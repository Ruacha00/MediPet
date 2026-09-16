# 前端固定源码导入与差异记录

内部执行记录，对应 [B02](specs/S01-baseline/issues/B02.md)。日期：2026-09-16；负责人：`spec_api_ui`。

## 固定输入与导入范围

- 来源仓库：EchoMindFrontend；固定提交：`9fe65b392ef44c50d71c856294bb77c41f0e86a5`，已用 Git 验证为有效 commit。
- 目标：当前 MediPet 工作树的 `frontend/`。来源工作区仅用于读取 Git 对象，前后 `git status --short` 均为空。
- 导入下表 10 个跟踪文件；未复制 `.git`、本地配置、依赖、构建结果或运行数据。
- 初次 Git archive 导出受当前 Windows 换行转换影响，原始 blob 核对发现 CRLF 差异；随后直接以 `git show <commit>:<path>` 的原始字节重写。最终 10/10 文件通过 `git hash-object --no-filters` 对照固定提交 blob ID。

| 来源路径 | 当前路径 | 固定提交 blob ID |
| --- | --- | --- |
| `.gitignore` | `frontend/.gitignore` | `f150b3199e19fc55dfe9c785f05d14e119fe8aa4` |
| `index.html` | `frontend/index.html` | `9e543ad7cbff763d9a9ad5ef3b423088166fd8ec` |
| `package.json` | `frontend/package.json` | `6f05ea112ecaed6ebb766850266bdb732b850880` |
| `package-lock.json` | `frontend/package-lock.json` | `bb2277d6a2be9ae7c7ce25daeba6b811c6874c41` |
| `public/runtime-config.js` | `frontend/public/runtime-config.js` | `56b53b2cacc4008e9d3c73e7a505ddffe40dfd0b` |
| `src/App.vue` | `frontend/src/App.vue` | `0533c963d32dacc8143a0ef93e80a163c82c95e5` |
| `src/lib/backends.js` | `frontend/src/lib/backends.js` | `d0e9bae331df30b5a212afb5143b1695194b762f` |
| `src/main.js` | `frontend/src/main.js` | `fdbdce560222c9de71a99bcaf384139f5d7b87e1` |
| `src/styles.css` | `frontend/src/styles.css` | `264e0f7155ed438371ed8faf9a93335740adbc44` |
| `vite.config.js` | `frontend/vite.config.js` | `07937ad8a4e1426a6ceb5b8e5e4063bd842ffcb2` |

固定提交另有 `.dockerignore`、`Dockerfile`、`README.md`、`docker-compose.yml`、`docker/entrypoint.sh`、`docker/nginx-gateway.conf`、`docker/nginx.conf` 七个文件。本步骤未导入这套独立部署入口；B03 在统一项目运行配置时读取固定来源并按实际需求适配，避免同时生效两套 Compose/说明。

## 原样基准构建证据

| 检查 | 实际结果 |
| --- | --- |
| Node / npm | `v22.20.0` / `11.11.1` |
| `npm --prefix frontend ci` | 成功，新增 35 个包，审计 36 个包；未改锁文件或升级依赖 |
| 已锁定直接依赖 | `@vitejs/plugin-vue@6.0.7`、`vite@7.3.5`、`vue@3.5.35` |
| `npm --prefix frontend run build` | 成功，Vite 转换 11 个模块；原样源码构建耗时 463 ms |
| 构建结果 | `dist/index.html` 0.46 kB；CSS 19.81 kB；JS 86.17 kB |
| 忽略验证 | `frontend/node_modules/.package-lock.json`、`frontend/dist/index.html` 均被 Git 忽略 |
| 源码差异 | 构建时 10 个导入文件与固定 Git blob 一致；无代码逻辑修改 |

安装输出报告 3 个依赖漏洞（1 low、2 high）。本步骤保留固定依赖，未自动运行升级或修复。此证据仅证明依赖安装及静态构建，不代表实际 API、容器代理、真实存储或业务浏览器验收通过。

## 当前工作树分析与已实施差异

分析绑定 `D:/Projects/Agent Learn/Project/MediPet-Rebuild`，分支 `refactor/medipet-scenario`；索引基于 `d0d2bdb` 加本次导入的工作树，1396 nodes / 2584 edges。未使用主工作区的旧索引。

- `backendMeta` 上游影响为 CRITICAL：12 个直接调用方、25 个受影响符号、12 个流程，覆盖聊天、知识、Skills、评测、监控与 trace。首次输出长度不足，已增大输出范围重取完整结果并核对所有直接调用方。
- `saveSettings` 为 CRITICAL：7 个符号、5 个流程；其直接调用方是 App.vue 的 `persist`。修改前已说明影响，保留请求函数签名和保存设置调用方式，仅改变单一服务选择与名称。
- `createInitialSettings`、`readSettings`、`runtimeConfig`、`checkHealth` 上游检查完成；`switchBackend`、`DEFAULT_BACKENDS` 为 UNKNOWN，已用当前源码核实模板按钮及所有常量读取点。Vite 配置未收录为可查询目标，按 package 脚本及代理源码核实消费关系。
- `DEFAULT_BACKENDS` → `PYTHON_BACKEND` 的 rename dry-run 命中同文件 4 个引用，已逐项审查后定点修改。运行时 `window` 属性的 rename 未找到图符号，源码搜索确认仅配置脚本写入与请求模块读取两处，定点改为 `__MEDIPET_CONFIG__`。
- 本步骤未重新索引；最终索引及提交前变更检查由 B04/主代理统一处理。

| 位置 | 基准现状 | 已实施调整 |
| --- | --- | --- |
| `src/lib/backends.js` | 多后端选择、未知类型默认落到 Java | 固定 Python；保留现有请求方法签名，默认 `/api/python`；保存的旧服务/地址不覆盖当前运行配置 |
| `src/App.vue` | 双后端按钮、`switchBackend`、健康失败时的服务回退 | 移除切换入口、切换函数及自动回退；失败如实保留原错误 |
| `vite.config.js` | 两套开发代理 | 仅保留 `/api/python`；上游来自 `MEDIPET_PYTHON_API_URL`，默认 `http://localhost:8000` |
| `public/runtime-config.js`、`src/lib/backends.js` | 原运行时全局键及双地址 | 统一 `window.__MEDIPET_CONFIG__`，仅保留 `pythonApiUrl` |
| `src/lib/backends.js` | 原项目本地存储键 | 使用 `medipet.frontend.settings`，保留用户/会话设置，不迁移旧品牌配置 |
| `package.json`、`package-lock.json` | 原项目包名 | 同步改为 `medipet-frontend`；与固定对象逐字段比较，仅名称变化，依赖数据完全一致 |
| `index.html`、`src/App.vue` | 原页面品牌 | 改为 MediPet 标题、标志和对话标题；既有布局/页面及场景示例保留给后续业务适配 |
| `src/styles.css` | 切换按钮专属样式 | 删除 `.backend-tabs` 相关样式，保留布局 |
| `tests/python-backend.test.mjs` | 无 | 使用 Node 内置测试与已锁 Vue/Vite 依赖覆盖请求、实际组件健康处理与开发代理，无新增依赖 |

## 调整后验收

- `npm --prefix frontend run build`：通过，Vite 7.3.5 转换 11 个模块；输出 HTML 0.45 kB、CSS 19.41 kB、JS 85.37 kB。
- `node --test frontend/tests/python-backend.test.mjs`：5/5 通过，覆盖全部 11 个请求方法及正文/文件、旧保存设置、运行时地址、实际编译 App.vue 的健康失败不回退、实际 Vite 转发到本地 HTTP 替身。
- 首轮代理测试因测试辅助代码没有解码含空格的文件 URL 而失败；改用 Node `fileURLToPath` 后整套 5 项通过，应用代码未因此新增分支。
- 基准包清单/锁文件与固定 Git 对象做 JSON 深比较：只允许 `name` 与根包名称改变，依赖版本、校验和及其余字段完全一致。
- 前端运行源、配置、包清单扫描：原品牌、Java 入口/变量、切换函数及旧自动回退引用均无命中。`node_modules` 和 `dist` 仍被忽略。
- 以上证明构建和请求/组件逻辑、真实开发代理到替身的连通；真实后端与容器代理由 B03/B04 接续，业务浏览器矩阵由 V04 验收。

## B03 运行配置交接

`index.html` 在应用模块之前加载 `/runtime-config.js`。容器生成文件应使用如下格式，保留同源代理前缀：

```javascript
window.__MEDIPET_CONFIG__ = {
  pythonApiUrl: '/api/python'
}
```

仅有 `pythonApiUrl` 字段。浏览器端不使用容器内部服务名；Nginx 将 `/api/python` 转发到 API 并移除前缀。开发模式由 Vite 读取 `MEDIPET_PYTHON_API_URL` 作为代理上游，默认 API 8000；此变量不作为浏览器直接请求地址。

## 当前交接状态

- B02 已完成，`frontend/` 写入范围已交还主代理/B03；品牌和 Python-only 调整已验证。
- 未提交、推送、修改后端或根运行配置。B03 可按上述契约接入统一容器运行方式。
