# 02: 实现端到端院内方位指引

Type: implementation
Status: needs-info
Blocked by: 01

**What to build:** 在真实 HospitalOperations Adapter 的权威契约上实现独立方位指引 Skill、只读 Tools、结构化卡片和历史恢复，不建设地图或路径计算。

- [ ] 实施前对 HospitalOperations 查询联合、HospitalToolProvider、能力 manifest、Runtime presenter 和 Web message part 运行 GitNexus impact analysis。
- [ ] 扩展 HospitalOperations 生产 Adapter 与契约测试，不预先给 Fake Adapter 发布业务数据。
- [ ] 新增 `hospital-wayfinding` Skill 及显式 Tool 绑定，严格执行精确匹配和无障碍模式规则。
- [ ] 新增 `data-hospital-wayfinding` presenter、TypeScript 契约和卡片，复用现有 stream 与 parts 历史持久化。
- [ ] 覆盖起点选择、精确命中、缺失组合、无障碍缺失、医院不可用及禁止隐式导诊。
- [ ] 仅在新增职责确实降低模块内聚时拆分 hospital tool 或 message-part 文件。
- [ ] 完成后运行 GitNexus `detect-changes --scope all`，任何 partial/truncated 结果必须重跑。

## Comments

- 在 01 和既定路线图前置切片满足前，本 issue 保持 `needs-info`。
