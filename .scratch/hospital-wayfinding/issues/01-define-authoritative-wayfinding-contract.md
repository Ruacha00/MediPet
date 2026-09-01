# 01: 明确本地虚构医院方位指引契约

Type: integration
Status: resolved

## Answer

个人开发阶段不建设真实医院 Adapter。`FakeHospitalOperations` 加载的版本化受控数据集，是“明和虚构医院”开发环境内的权威来源；模型只能原样使用精确匹配结果，不能补全、拼接或反转。契约覆盖稳定起点、服务地点、普通/无障碍模式、typed errors 和数据版本，不引入建筑结构。

## Comments

- 2026-09-01：用户确认放弃真实医院 Adapter，并同意以本地虚构医院数据完成该切片。
