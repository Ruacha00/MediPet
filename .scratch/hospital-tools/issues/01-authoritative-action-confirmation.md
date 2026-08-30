# 01: 为写 Tool 建立权威患者作用域与确认快照

**What to build:** 扩展通用 Tool 执行与 Action Proposal 协议，使患者身份来自已校验的就诊事项，并允许写 Tool 在持久化前生成服务器权威确认快照、在提交前重新校验该快照。

**Type:** implementation

**Status:** claimed

- [ ] 就诊存储可以通过一次已校验读取返回患者 ID 与就诊阶段，`ToolContext` 携带患者 ID。
- [ ] `ActionProposal` 持久化患者 ID 和独立确认快照；内存与 PostgreSQL 实现及 migration 保持一致。
- [ ] 写 Tool 支持服务器侧准备、Schema 验证和提交前重新校验确认快照，而无需把权威资料加入模型参数。
- [ ] 相同请求重试返回原提案，患者、阶段、Tool 版本、快照变化、过期或不可用均拒绝旧提案。
- [ ] Action Store 保持医院无关，通用读 Tool 和现有写 Tool 行为不回归。
- [ ] 公共契约测试、API 测试、Ruff 和 Pyright 通过。
