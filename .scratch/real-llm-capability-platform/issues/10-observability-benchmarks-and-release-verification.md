# 10: 加入去标识化指标并完成发布验证

**What to build:** 让维护者能够区分 MediPet 自身开销与外部模型性能，运行可重复的 fake/live benchmark，并通过完整后端、前端、迁移和浏览器验证确认本规格可交付。

**Blocked by:** 03/支持开发环境 .env 热加载, 06/导入、导出并隔离 Skill 包, 08/暂停并确认有副作用的 Tool, 09/完善流式取消、重试和运行预算.

**Status:** resolved

- [x] 记录首 token 延迟、总耗时、模型耗时、Tool 耗时、模型请求次数、token 用量、Agent 步数和终止结果。
- [x] 指标按 provider、model 和 Runtime Profile 版本区分。
- [x] 日志、指标和 benchmark 不记录参与者原文、完整 Prompt、API Token、数据库 URL 或上游错误正文。
- [x] deterministic fake model benchmark 可以稳定测量应用、SSE、数据库和 Runtime 自身开销。
- [x] 手动 live-model benchmark 只在显式配置后运行，并输出按模型区分的去标识化报告。
- [x] CI 不访问真实或收费模型，也不设置跨供应商绝对延迟门槛。
- [x] 全部数据库 migrations 能在空测试数据库应用，并能验证重启恢复。
- [x] 后端测试、格式检查、lint 和类型检查全部通过。
- [x] Web 单元测试、lint、生产构建和更新后的浏览器 smoke 全部通过。
- [x] 示例环境文件和运行文档不包含真实密钥，并明确要求只使用虚构测试数据。
- [x] 最终验证确认开发和生产 seed 均不包含业务 Skill、业务 Tool 或 fake 医院数据。

## Answer

已加入去标识化运行指标、PostgreSQL 持久化与管理查询端点，提供显式隔离的 fake/live benchmark 和不访问外部模型的 CI。发布验证已从空 PostgreSQL 应用全部迁移并通过 117 项后端测试、Ruff、Pyright、Web 单测、ESLint、生产构建和 Chromium smoke；独立 seed 验证确认只创建患者、参与者和就诊事项，Skill 与 Tool 均为零。
