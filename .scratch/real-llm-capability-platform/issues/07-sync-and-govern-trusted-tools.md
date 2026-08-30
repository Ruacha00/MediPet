# 07: 同步和治理受信任 Tools

**What to build:** 让部署代码提供的受信任 ToolProvider 把版本化 Tool 契约同步到管理平台，使管理员能够查看、启停和绑定，而不能通过后台上传代码或任意 URL 创建执行能力。

**Blocked by:** 04/建立零业务能力的完整 ReAct Runtime, 05/管理版本化 instruction-only Skills.

**Status:** resolved

## Answer

已实现由部署 `ToolProvider` 驱动的受信 Tool Registry：支持不可变版本同步、实现缺失与契约漂移检测、管理员查看/启停/审批配置、Skill 版本绑定与发布校验、运行时权限交集及执行前复验、PostgreSQL 持久化，以及同步、版本、启停、绑定、调用和拒绝审计。

管理 API 仅接受治理字段，不提供代码上传、任意执行 URL 或覆盖平台检查的入口；空 Provider 和空 Registry 均为合法状态。

- [x] ToolProvider 能同步稳定 Tool ID、版本、描述、输入输出 Schema、read/write 影响和审批元数据。
- [x] 空 ToolProvider 和零业务 Tool Registry 是合法状态。
- [x] Tool 实现消失或 Schema 改变时创建显式状态或新版本，不静默修改已发布 Skill 使用的契约。
- [x] 管理员可以查看、启用、停用和配置 Tool 审批元数据。
- [x] 管理员不能修改 Tool 实现、上传代码、填写任意执行 URL 或覆盖平台检查。
- [x] tool-assisted Skill 只有在绑定的 Tool 存在、启用且版本兼容时才能发布。
- [x] Runtime 使用 Skill 绑定、环境启用、参与者权限和就诊阶段允许集合的交集，并在执行前再次校验。
- [x] 测试专用 Tool 可以通过完整同步、绑定、发布和 ReAct 调用路径工作。
- [x] Tool 同步、版本变化、启停、绑定和调用拒绝均产生审计记录。
