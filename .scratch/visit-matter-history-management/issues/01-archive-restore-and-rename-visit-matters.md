# 01: 归档、恢复并改名就诊事项

Type: implementation
Status: resolved

**What to build:** 在保持消息、操作和审计记录不变的前提下，为参与者增加就诊事项改名、归档、已归档列表和恢复能力，并修复 Web 对固定 demo 事项及空列表的错误假设。

- [x] 实施前重新对 `visit_context`、`VisitConversationStore`、`VisitMatterRecord`、`VisitMatterSummary` 和 `ChatShell` 运行 GitNexus impact analysis；HIGH 或 UNKNOWN 结果按仓库规则处理。
- [x] 增加 Alembic 迁移和事项归档 lifecycle，保留现有标题字段并实现医院本地时间默认标题。
- [x] 扩展 Store 契约及 In-memory/PostgreSQL 实现，覆盖改名、活动/归档列表、归档和恢复。
- [x] 增加参与者范围内的 HTTP 契约，并拒绝归档忙碌事项或通过已归档事项继续聊天和确认操作。
- [x] Web 增加历史记录管理、已归档入口、恢复与真实空状态，初始历史必须与选中的事项 ID 一致。
- [x] 保证改名、归档和恢复不改变最近消息活动排序，不删除或改变任何医院动作。
- [x] 增加 Store、迁移、HTTP、组件和浏览器回归测试。
- [x] 完成后运行 GitNexus `detect-changes --scope all`，任何 partial/truncated 结果必须重跑。

## Comments

- 本 issue 不包含永久删除；产品行为以同目录 `spec.md` 为准。

## Answer

已实现参与者范围内的历史记录改名、可逆归档、已归档列表与恢复；归档不删除消息、Action Proposal、收据或审计。后端按医院本地时间生成默认标题，持久化层保持最近消息活动排序，并通过 visit row 锁阻止归档与消息写入并发穿透。HTTP 使用 ActionStore 权威状态和运行中确认窗口拒绝忙碌归档，已归档事项保持只读。

Web 已移除固定 demo fallback，支持桌面/移动端历史管理、真实空状态、初始列表失败与生命周期刷新失败状态；生命周期变化后以服务端列表恢复权威排序。Store、HTTP、组件、PostgreSQL 并发与浏览器回归均已覆盖。
