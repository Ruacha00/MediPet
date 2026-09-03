# 02: 实现端到端院内方位指引

Type: implementation
Status: resolved
Blocked by: 01

**What to build:** 在本地虚构医院的权威契约上实现独立方位指引 Skill、只读 Tools、结构化卡片和历史恢复，不建设地图、建筑结构或路径计算。

- [x] 实施前对 HospitalOperations 查询联合、HospitalToolProvider、能力 manifest、Runtime presenter 和 Web message part 运行 GitNexus impact analysis。
- [x] 扩展 HospitalOperations 与 Fake Adapter 的受控数据契约。
- [x] 新增 `hospital-wayfinding` Skill 及显式 Tool 绑定，严格执行精确匹配和无障碍模式规则。
- [x] 新增 `data-hospital-wayfinding` presenter、TypeScript 契约和卡片，复用现有 stream 与 parts 历史持久化。
- [x] 覆盖起点选择、精确命中、缺失组合、无障碍缺失、医院不可用及禁止隐式导诊。
- [x] 仅在新增职责确实降低模块内聚时拆分 hospital tool 或 message-part 文件。
- [x] 完成后运行 GitNexus `detect-changes --scope all`，任何 partial/truncated 结果必须重跑。

## Answer

已以 `FakeHospitalOperations` 的版本化受控数据完成端到端院内方位指引：HospitalOperations 查询契约、三个只读 Tool、独立 Skill、结构化流式卡片、通用历史恢复和桌面/移动端展示均已实现。没有加入真实医院 Adapter、建筑结构、地图、路径计算、生产身份权限或 LangGraph checkpoint。

## Comments

- 2026-09-01：真实医院 Adapter 已从个人开发路线图移除；01 已解决，本 issue 已领取。
