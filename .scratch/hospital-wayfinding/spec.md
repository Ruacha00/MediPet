# 本地虚构医院的院内方位指引

Type: spec
Status: resolved

## Problem Statement

MediPet 已有 `data-hospital-route` 的前端展示壳，但后端没有常用起点、院内服务地点、可信文字指引或业务 Tool。个人开发阶段不接入真实医院 Adapter；同时不能让模型凭知识生成、拼接或反转方位说明。

本切片只提供从命名起点到明确院内服务地点的有序文字指引，不建设建筑结构、室内地图、实时定位或路径计算，也不根据症状替参与者选择科室。

## Decision

- `FakeHospitalOperations` 加载的受控本地数据集，是“明和虚构医院”开发环境内的权威来源；这里的“权威”只表示应用照原文返回受控数据，不表示真实世界医院信息。
- 数据集声明稳定起点 ID、服务地点 ID、`standard` / `accessible` 模式、原文步骤和数据版本。
- Tool 只做精确起点—目的地—模式匹配。无效 ID、缺失组合、缺失无障碍版本和医院不可用使用不同 typed error。
- 不明确起点时先列出受控起点；当前数据可以只有一个默认起点，但契约支持多个起点。
- 指引只接受参与者已明确的服务地点；症状陈述不能触发自动科室选择或隐式方位指引，继续遵守 ADR-0008。
- 新建独立 `hospital-wayfinding` Skill，只绑定三个只读 Tool：列出常用起点、列出院内服务地点、读取精确方位指引。
- Web 使用 `data-hospital-wayfinding` 结构化消息部分。流式响应、消息持久化和历史恢复复用现有通用 parts 机制。
- 只在新增职责确实降低现有模块内聚时拆分文件；目录数量和对齐完整目标架构不是验收条件。

## Data Contract

方位指引结果包含：

- 起点与目的地的稳定 ID、显示名称；
- `standard` 或 `accessible` 模式；
- 非空、有序、未经模型改写的文字步骤；
- 可选提醒；
- 本地数据版本。

文字可以提及入口、楼层、电梯和可见地标，但这些内容始终只是医院数据里的原文，不解析为 Building、Floor、Room、Node 或 Edge。

## Acceptance Criteria

- [x] HospitalOperations 可列出本地权威起点和服务地点，并精确查询普通或无障碍文字指引。
- [x] 无效 ID、缺失组合、缺失无障碍版本和医院不可用具有不同 typed error。
- [x] 独立 `hospital-wayfinding` Skill 只绑定三项只读 Tool。
- [x] 精确命中产生 `data-hospital-wayfinding` 卡片，刷新后可从历史恢复。
- [x] 未命中时不拼接、反转、猜测或生成新指引。
- [x] 症状陈述不触发自动科室选择或隐式方位指引。
- [x] 契约、Tool、Skill、Runtime presenter、HTTP history、Web 卡片和浏览器流程均有测试证据。

## Out of Scope

- 真实医院 Adapter 与任何外部医院集成。
- 生产身份、权限与人工转介。
- LangGraph checkpoint。
- Building、Floor、Room、Node、Edge 或其他可查询建筑结构。
- 室内地图、实时定位、路径搜索、动态绕行、距离和预计时间。
- 模型生成、拼接、反转或纠正医院指引。
- 症状到科室的自动匹配或推荐。
- 为对齐 `architecture.md` 而进行无关模块重构。
