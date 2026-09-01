# 医院认可的院内方位指引

Type: spec
Status: needs-info

## Problem Statement

MediPet 的领域边界允许到院后的院内协助，但当前只有 Web `data-hospital-route` 展示壳，后端没有院内服务地点、常用起点、可信文字指引、HospitalOperations 查询或业务 Tool。旧架构文档使用“route/navigation”语言，容易被误读为需要建筑结构、地图节点和路径计算。

本能力只提供服务医院认可的文字方位说明。它不建设室内地图、建筑结构模型、实时定位或自动路径规划，也不能借方位指引绕过“不自动根据症状选择科室”的边界。

## Solution

- 领域使用“院内服务地点”和“院内方位指引”，不使用“院内路线”描述该能力。
- 真实医院 Adapter 提供医院认可的常用起点、院内服务地点以及精确起点—目的地—模式组合对应的有序文字步骤。
- 服务地点覆盖科室以及药房、收费处、检验、影像等常用目的地，不承诺任意房间和设施。
- 文字步骤可以提及楼栋、楼层、扶梯、电梯或地标，但这些内容保持为医院认可文本，不解析成 Building、Floor、Node 或 Edge 对象。
- 医院可以为同一起终点分别提供普通与无障碍版本。缺少所需版本时明确返回 unavailable，不由模型改写楼梯路线或推断替代路线。
- 参与者未说明起点时，若医院仅配置一个常用起点则直接使用；存在多个时返回可选起点并等待明确选择。
- Tool 只做精确匹配，不拼接、反转或模糊匹配已有文字。缺少组合时列出可用起点，必要时建议询问院内工作人员。
- 方位指引只接受已经明确的服务地点，不根据症状选择或推荐科室，继续遵守 ADR-0008。

## Capability Boundary

- 新建独立的 `hospital-wayfinding` Skill，不扩张医院服务目录 Skill 的职责。
- Skill 绑定只读 Tool：列出常用起点、列出院内服务地点、读取精确方位指引。Tool 使用现有 namespaced 命名，例如 `hospital.get_wayfinding_guidance`。
- 底层扩展现有 `HospitalOperations` 查询联合和生产 Adapter；不为方位指引创建平行的医院 Adapter 体系。
- 测试可以注入确定性 HospitalOperations double，但仓库的 Fake Adapter 不先于真实医院 Adapter 发布该业务能力。
- 新能力可以促使 `hospital/tools.py` 或消息卡片按职责拆分，但目录数量和对齐 `architecture.md` 不是验收条件。

## Data Contract

方位指引结果至少包含：

- 稳定的起点 ID 与显示名称；
- 稳定的目的地 ID 与显示名称；
- `standard` 或 `accessible` 模式；
- 非空、有序的医院认可文字步骤；
- 可选提醒，例如夜间入口关闭；
- 医院数据版本或更新时间。

Web 使用新的 `data-hospital-wayfinding` 结构化消息部分展示这些字段。它不展示地图、计算距离、预计时间或实时状态。通用消息 parts 持久化与历史恢复继续复用现有机制。

## Implementation Gate

该切片遵守已经确认的路线图顺序：真实医院 Adapter、身份/权限与人工转介先于院内方位指引。开始实现前，必须获得生产 HospitalOperations Adapter 可提供的权威数据来源、标识和版本契约；在此之前保持 `needs-info`，不以模型知识或仓库 fake 数据代替医院认可内容。

## Acceptance Criteria

- [ ] HospitalOperations 契约可列出权威常用起点和服务地点，并精确查询普通或无障碍文字指引。
- [ ] 无效 ID、缺失组合、缺失无障碍版本和医院不可用使用不同的 typed error。
- [ ] 独立 `hospital-wayfinding` Skill 只绑定允许的只读 Tool，未启用或未发布时不会向模型暴露能力。
- [ ] 起点不明确时返回可信选项；只有一个起点时可以确定性选择。
- [ ] 精确命中时流式响应包含 `data-hospital-wayfinding` 卡片，刷新后可从历史恢复。
- [ ] 未命中时不拼接、反转、猜测或生成新路线，并给出安全的院内人工询问建议。
- [ ] 普通和无障碍版本仅来自医院数据，模型不能自行转换。
- [ ] 症状陈述不能触发自动科室选择或隐式方位指引。
- [ ] HospitalOperations 契约、Tool、Skill、Runtime presenter、HTTP stream、Web 卡片和浏览器测试覆盖完整流程。

## Out of Scope

- Building、Floor、Room、Node、Edge 或可查询建筑结构。
- 室内定位、地图、路径搜索、动态偏航、距离和预计时间计算。
- 施工、拥堵、电梯故障等实时状态，除非未来真实 Adapter 提供权威数据并重新规划。
- 模型生成、拼接、反转或纠正医院指引。
- 症状到科室的自动匹配或推荐。
- 在真实医院 Adapter 之前把该能力加入 Fake Adapter 或开发默认 Skill。
- 为实现目标目录而进行无关模块重构。
