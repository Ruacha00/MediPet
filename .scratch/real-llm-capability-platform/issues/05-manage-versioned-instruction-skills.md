# 05: 管理版本化 instruction-only Skills

**What to build:** 让系统管理员通过受保护的管理 API 创建、审核、发布、停用和回滚纯指令 Skill，同时保证空 Registry 合法、已发布版本不可变、运行中的 turn 不受版本切换影响。

**Blocked by:** 02/持久化并恢复就诊事项对话, 04/建立零业务能力的完整 ReAct Runtime.

**Status:** resolved

- [x] 没有任何业务 Skill 时，列表、运行时发现和普通聊天均正常工作。
- [x] 管理 API 使用配置的 Bearer Token，缺失或错误 Token 会被安全拒绝。
- [x] production 在没有正式医院身份接入时不会开放开发管理认证模式。
- [x] instruction-only Skill 可以经历 draft、in_review、published 和 retired 生命周期。
- [x] 已发布版本不可修改，任何编辑都会产生新的草稿版本和变更说明。
- [x] 管理员可以激活旧版本实现回滚，已有 turn 仍使用开始时固定的版本。
- [x] Runtime 只发现已发布 Skill，并按需加载其完整指令而不是把所有内容常驻上下文。
- [x] Skill 指令不能覆盖平台不可变约束、领域边界或受保护 SafetyPolicy 命名空间。
- [x] 创建、审核、发布、停用、激活和运行时选择均产生审计记录。
- [x] 测试 Skill 只存在于隔离测试数据，不进入开发 seed。

## Answer

已实现受 Bearer Token 保护的 instruction-only Skill 管理 API、不可变版本生命周期、
回滚与完整审计；production 不注册开发管理路由。Runtime 仅发现已激活的 published
版本，先提供名称与描述，并通过受约束的 `load_skill` 按需加载 turn 开始时固定版本的
完整指令。PostgreSQL 模型与 Alembic migration 保存 Skill、版本和审计，开发 seed
保持零业务 Skill。
