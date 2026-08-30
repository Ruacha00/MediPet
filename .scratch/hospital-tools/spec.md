# 注册医院查询与预约挂号 Tool

MediPet 需要把已经完成的 `HospitalOperations` 能力登记为受信任 Tool，供后续 Skill 绑定和业务 ReAct 激活使用。本切片只建立 Tool 契约、权威患者作用域和预约确认提案能力；所有新 Tool 同步后保持默认禁用，不改变当前聊天行为。

## 用户可见目标

- 后台可以登记并查看医院资料、科室、医生、号源及当前患者预约的查询 Tool。
- 创建预约挂号必须先向就诊参与者展示患者、服务医院、科室、医生、时间和费用，再等待明确确认。
- 患者身份来自当前就诊事项，模型不能提供、覆盖或切换患者 ID。
- 确认时若号源已被占用或确认信息发生变化，不得自动改约其他号源；旧提案失败并要求重新选择。
- 当前部署使用独立虚构医院数据，未来可在不改变 Tool 契约的前提下替换真实 `HospitalOperations` Adapter。

## Tool 契约

首个版本统一为 `1`，Registry ID 使用点号命名空间，模型调用名使用下划线：

| Registry ID | 模型调用名 | Effect | 允许阶段 |
| --- | --- | --- | --- |
| `hospital.get_hospital` | `hospital_get_hospital` | read | 诊前、诊中 |
| `hospital.list_departments` | `hospital_list_departments` | read | 诊前、诊中 |
| `hospital.list_doctors` | `hospital_list_doctors` | read | 诊前、诊中 |
| `hospital.search_slots` | `hospital_search_slots` | read | 诊前、诊中 |
| `hospital.get_appointment` | `hospital_get_appointment` | read | 诊前、诊中 |
| `hospital.list_appointments` | `hospital_list_appointments` | read | 诊前、诊中 |
| `hospital.create_appointment` | `hospital_create_appointment` | write | 仅诊前 |

查询 Tool 使用稳定对象信封：`hospital`、`departments`、`doctors`、`slots`、`appointment` 或 `appointments`。患者范围内查不到单个预约时返回 `appointment: null`，不得泄漏其他患者是否拥有该预约。创建成功返回医院收据 ID 与预约对象。

日期时间使用带 UTC 偏移量的 ISO 8601 字符串；金额继续使用人民币分整数 `fee_cents` 和 `currency: "CNY"`。列表没有结果时返回空数组。无效输入由 Schema 或 typed validation 拒绝，医院服务不可用及提交冲突走失败路径。

## 权威作用域与确认

- 就诊存储一次性校验就诊事项与参与者，并返回同一快照中的患者 ID 和就诊阶段。
- `ToolContext` 携带权威患者 ID；患者范围内的查询和提交不接受模型提供的患者 ID。
- 写 Tool 可以在提案持久化前，根据模型给出的最小业务参数生成服务器权威确认快照。
- `hospital.create_appointment` 的模型输入只包含 `slot_id`；确认快照包含患者、服务医院、科室、医生、开始及结束时间、费用和币种。
- `ActionProposal` 独立持久化并输出确认快照，不能把快照伪装为模型参数。
- 同一请求的重试返回已经持久化的同一提案；不得用重新查询到的数据静默改写提案。
- 确认时按已持久化快照重新校验患者作用域、Tool 版本、执行阶段和号源资料。任何变化、过期或占号都拒绝旧提案。
- 医院查询属于 Tool Adapter 或动作编排职责；`ActionStore` 保持通用持久化边界，不依赖医院模块。

## 注册与激活边界

- 新增接收 `HospitalOperations` 的医院 Tool Provider；生产组装入口当前注入 `FakeHospitalOperations`。
- 应用启动时将 Provider 契约同步至受信任 Tool Registry。
- 新同步的 Tool 保持 Registry 既有的默认禁用语义；写 Tool 永远要求审批。
- 本切片不创建或发布 Skill，不绑定 Tool，不启用 Tool，不改变 Agent 提示、聊天能力或前端业务流程。
- 本切片不增加医院 Adapter 环境变量，不增加真实医院集成。

## 验收边界

- 七个 Tool 的身份、版本、输入输出 Schema、effect、审批和阶段限制均有公共契约测试。
- 测试证明患者 ID 与医院幂等键均来自执行上下文，而不是模型参数。
- 测试证明预约确认展示权威快照，确认前不占号，确认后只创建一次预约。
- 测试证明快照变化、号源已占、患者作用域变化、过期和重复确认的行为。
- 内存与 PostgreSQL Action Store 对新增患者作用域、确认快照和幂等语义通过相同契约测试。
- 启动同步后可以通过管理接口看到七个默认禁用的 Tool，现有聊天行为保持不变。
- API 测试、Ruff 和 Pyright 通过。
