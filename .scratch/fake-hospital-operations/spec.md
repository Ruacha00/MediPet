# 独立虚构医院数据源与 FakeHospitalOperations

MediPet 需要一个与 PostgreSQL seed、Agent Runtime、Skill 和 Tool 注册相互独立的虚构医院数据源，用于后续医院能力开发和确定性测试。当前切片只建立医院外部边界及 fake adapter，不激活聊天业务能力。

## 能力范围

- 仓库内只读 JSON 描述唯一的服务医院、科室、医生、相对排班和初始预约占号。
- `HospitalOperations` 通过 typed `query`/`commit` 契约查询医院资料、科室、医生、可用号源和患者范围内的预约。
- 首个写操作仅创建预约；取消预约、院内路线、科室导诊、人工转介、Tool、Skill 和 Runtime wiring 均不在本切片内。
- 号源基于注入时钟生成未来两周的 `Asia/Shanghai` 时间；金额使用人民币分整数。
- `FakeHospitalOperations` 的预约状态仅在实例内存在，新实例从 JSON 初始状态重新开始。

## 行为约束

- JSON 加载时校验唯一 ID、引用关系、时区、排班时段、费用及初始预约占号。
- 号源搜索只返回尚可预约的号源；创建预约使用号源中的科室、医生、时间和费用快照。
- 单号源只能被成功占用一次，并发提交必须原子化。
- 相同幂等键与相同参数返回同一收据；相同键与不同参数产生幂等冲突。
- 预约查询必须由患者 ID 限定，不能跨患者泄漏预约是否存在。
- 无效请求、资源不存在、号源不可用、幂等冲突和服务不可用使用不同的 typed error。
- 测试可以注入确定次数的查询或提交故障，故障发生前不得产生预约副作用。

## 验收边界

- 不修改现有“医院数据尚未配置”的系统提示或聊天行为。
- 不把虚构医院数据写入 PostgreSQL seed，也不增加数据库 migration。
- 不注册业务 Tool 或 Skill，不解析 `MEDIPET_HOSPITAL_ADAPTER`。
- 公共契约测试、Ruff 和 Pyright 必须通过。
