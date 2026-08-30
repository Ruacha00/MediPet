# 02: 注册医院查询与创建预约挂号 Tool

**What to build:** 将 `HospitalOperations` 适配为规格定义的六个查询 Tool 和一个创建预约挂号 Tool，通过受信任 Provider 在应用启动时同步，并以虚构医院 Adapter 提供当前实现。

**Type:** implementation

**Status:** ready-for-agent

**Blocked by:** 01

- [ ] 注册七个版本 `1` 的 Tool，身份、模型调用名、effect、审批和允许阶段符合规格。
- [ ] 查询 Tool 使用稳定对象信封、ISO 8601 时间、人民币分金额及患者范围内的未找到语义。
- [ ] 预约查询与创建从 `ToolContext` 取得患者 ID；创建操作从上下文取得医院幂等键，模型输入仅包含 `slot_id`。
- [ ] 创建预约提案展示患者、服务医院、科室、医生、时间和费用的权威快照，并在确认时重新校验。
- [ ] 当前应用组装注入 `FakeHospitalOperations`，启动同步后七个 Tool 可在管理接口查看且默认禁用。
- [ ] 不创建 Skill、Tool 绑定或聊天业务激活，不增加真实医院 Adapter 或选择环境变量。
- [ ] Provider、执行适配、Registry 同步和确认流程测试覆盖成功、空结果、未找到、服务失败、占号及幂等场景。
- [ ] API 测试、Ruff 和 Pyright 通过。
