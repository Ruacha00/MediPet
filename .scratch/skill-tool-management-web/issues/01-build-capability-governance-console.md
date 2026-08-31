# 01: 构建 Skill / Tool 能力治理台

Type: implementation
Status: resolved

**What to build:** 按 `.scratch/skill-tool-management-web/spec.md` 实现仅非生产可用的桌面能力治理台，复用现有管理 API，补齐安全资源预览、Tool 绑定查询/解绑和 draft-only 绑定冻结，并覆盖公开 HTTP、Web 交互及 Compose 浏览器接缝。

- [x] FastAPI 支持绑定查询、draft 解绑和安全文本资源读取。
- [x] Tool 绑定只允许 draft 修改，内存与 PostgreSQL 行为一致。
- [x] Next.js 服务端集中持有管理 Token，并提供显式同源管理 handlers。
- [x] Skills、Skill 详情/新建、Tools、Tool 详情和统一审计页面完成。
- [x] 生命周期、版本比较、资源预览、绑定与 Tool 配置流程符合规格。
- [x] 1280×720 与 1440×900 浏览器烟测通过，不调用真实模型。
- [x] 后端与 Web 的测试、类型检查、lint 和构建通过。

## Answer

已交付仅显式 development/test 环境可用的 Skill / Tool 能力治理台。Next.js 通过集中授权接缝和私有 Token 复用 FastAPI 管理 API；FastAPI 补齐绑定查询、draft 解绑、安全资源预览和审核后冻结，并在 PostgreSQL 绑定变更中锁定 Skill 版本行以消除审核竞态。Skills、Tools、统一审计、版本比较、确认与错误状态均已实现。

验收结果：API `223 passed, 9 skipped`（跳过项需要外部 `MEDIPET_TEST_DATABASE_URL`），Web `32 passed`；Ruff、Pyright、ESLint、TypeScript 和 Next production build 全部通过；Compose 浏览器烟测覆盖 1280×720、1440×900 及完整治理闭环。

## Comments

- 2026-08-31: 已认领，固定实现前基线为 `84d5d75f764548de73a9c4fc8bf9d4c350835213`。
- 2026-08-31: Compose 浏览器闭环已通过，覆盖全部治理路由的两档桌面尺寸与刷新恢复。
- 2026-08-31: 双轨 Standards / Spec 审查完成并修正生产 fail-closed、请求前授权、并发冻结、确认可访问性和筛选一致性问题；工单已解决。
