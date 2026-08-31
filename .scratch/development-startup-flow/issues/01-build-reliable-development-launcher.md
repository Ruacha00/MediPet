# 01: 建立可靠的本地开发启动器

Type: implementation
Status: resolved

**What to build:** 以一个 Windows 根目录命令编排 Compose PostgreSQL、宿主机 API/Web、依赖同步、迁移、虚构开发 seed、健康检查、日志和联停，并同步启动文档。

- [x] 未提供外部数据库 URL 时自动准备持久化开发 PostgreSQL。
- [x] 每次按 lockfile 增量同步 API 与 Web 依赖。
- [x] 默认迁移和 seed，且跳过选项语义明确。
- [x] 等待 API 存活和 Web 可访问，并独立报告 readiness。
- [x] 端口冲突、缺少工具和模型未配置均有可操作反馈。
- [x] API/Web 日志带前缀，退出和失败时可靠联停。
- [x] README、架构文档、ADR 和启动实现一致。

## Answer

已建立以 `start-dev.ps1` 为标准入口的 Windows 本地启动流程：脚本在没有外部数据库 URL 时启动持久化 Compose PostgreSQL，随后按 lockfile 同步依赖、应用迁移和幂等开发 seed，并监督带前缀日志的宿主机 API/Web。启动器等待 API liveness 与 Web 响应、单独报告 readiness，支持端口和超时覆盖、打开浏览器、跳过准备步骤以及退出时停止数据库；受控失败与完整成功烟测均确认不会遗留应用监听进程。README、Accepted 架构和 ADR 0005 已同步；Next 16 自动生成的 `next-env.d.ts` 保持跟踪并提交开发模式生成状态，避免 `next dev` 污染工作树。
