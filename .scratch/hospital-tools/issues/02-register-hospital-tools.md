# 02: 注册医院查询与创建预约挂号 Tool

**What to build:** 将 `HospitalOperations` 适配为规格定义的六个查询 Tool 和一个创建预约挂号 Tool，通过受信任 Provider 在应用启动时同步，并以虚构医院 Adapter 提供当前实现。

**Type:** implementation

**Status:** resolved

**Blocked by:** 01

- [x] 注册七个版本 `1` 的 Tool，身份、模型调用名、effect、审批和允许阶段符合规格。
- [x] 查询 Tool 使用稳定对象信封、ISO 8601 时间、人民币分金额及患者范围内的未找到语义。
- [x] 预约查询与创建从 `ToolContext` 取得患者 ID；创建操作从上下文取得医院幂等键，模型输入仅包含 `slot_id`。
- [x] 创建预约提案展示患者、服务医院、科室、医生、时间和费用的权威快照，并在确认时重新校验。
- [x] 当前应用组装注入 `FakeHospitalOperations`，启动同步后七个 Tool 可在管理接口查看且默认禁用。
- [x] 不创建 Skill、Tool 绑定或聊天业务激活，不增加真实医院 Adapter 或选择环境变量。
- [x] Provider、执行适配、Registry 同步和确认流程测试覆盖成功、空结果、未找到、服务失败、占号及幂等场景。
- [x] API 测试、Ruff 和 Pyright 通过。

## Answer

已将 `HospitalOperations` 注册为六个查询 Tool 和一个创建预约挂号 Tool。查询输出采用完整稳定 Schema 与对象信封，患者作用域和医院幂等键由 `ToolContext` 提供；创建预约使用服务器准备的权威确认快照并在提交前重新校验。当前应用注入 `FakeHospitalOperations`，启动同步后七个版本 `1` 的 Tool 可通过管理接口查看且保持默认禁用。Provider、执行适配、Registry 同步、确认/占号/幂等场景、完整 API 测试、Ruff 与 Pyright 均已通过。
