# 08: 暂停并确认有副作用的 Tool

Type: implementation
Status: resolved
Blocked by: 02, 07

**What to build:** 让任何 write Tool 在执行前生成精确、持久化且可审核的 Action Proposal，并且只有就诊参与者明确确认后才能幂等提交；本阶段用 dummy write Tool 证明完整行为。

- [x] Tool 契约明确区分 read 和 write，模型或 Skill 不能自行改变影响等级。
- [x] write Tool 的首次调用只创建 Action Proposal，不执行实际副作用。
- [x] proposal 包含参与者、就诊事项、精确参数、Tool 版本、过期时间和幂等信息。
- [x] Runtime 在 proposal 产生后暂停当前循环并返回既有确认交互可消费的数据。
- [x] confirm、reject、过期、错误作用域和不存在 proposal 均产生明确且安全的结果。
- [x] 相同确认重复提交只生成同一个 receipt，不重复执行 Tool。
- [x] 参数、Tool 版本或作用域变化会使原确认失效。
- [x] receipt 和审计记录先持久化，之后才向参与者报告成功。
- [x] write Tool 不自动重试；重复请求由幂等协议处理。
- [x] dummy write Tool 只用于自动测试，不进入开发或生产 Registry。

## Answer

实现了通用 write Tool 两阶段协议：Runtime 固定受信任 Tool 的 effect/version/参数并持久化
Action Proposal 后暂停；确认入口重新校验参与者、就诊事项、过期时间、Tool 版本和运行时作用域，
使用 proposal 专属幂等键提交，原子持久化 receipt 与审计后才返回成功。重复确认复用同一 receipt，
拒绝、过期、不存在、作用域错误、版本漂移和执行失败均返回安全终态。内存与 PostgreSQL 存储遵循
同一公开契约；dummy write Tool 仅存在于自动化测试。
