# 02: 持久化并恢复就诊事项对话

**What to build:** 让同一就诊事项的参与者消息和助手消息由 PostgreSQL可靠保存，并在刷新页面或重启 API 后恢复已完成对话，而不会把失败或取消内容带入下一轮模型上下文。

**Blocked by:** 01/接通真实模型流式对话.

**Status:** resolved

- [x] 数据库迁移可以从空 PostgreSQL 实例建立患者、就诊参与者、就诊事项和消息所需结构。
- [x] 独立开发 seed 操作创建演示患者、就诊参与者和就诊事项，但不创建业务 Skill 或 Tool。
- [x] 参与者消息在模型调用前持久化，助手消息经历 pending、streaming 和 completed、failed 或 cancelled 终态。
- [x] 助手文本以批次持久化，不要求每个 token 单独提交事务。
- [x] 只有同一就诊事项的 completed 消息进入下一轮模型上下文，默认最多最近二十条。
- [x] API 重启后，历史读取仍能返回同一就诊事项的已完成消息及失败、取消状态。
- [x] Web 刷新后恢复历史，并且不会把半截回答显示为已完成。
- [x] 并发写入不会让较旧 turn 静默覆盖较新就诊事项状态。
- [x] Alembic 迁移和 PostgreSQL-backed Store 具有可重复的契约测试。

## Answer

已实现 PostgreSQL 对话持久化、Alembic 迁移、幂等开发 seed、Assistant 批量写入与完成历史上下文、历史读取 API 和 Web 刷新恢复。PostgreSQL 17 一次性实例上的迁移、Store 重建恢复和并发交错契约测试均已通过。
