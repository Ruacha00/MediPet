# 03: 支持开发环境 .env 热加载

**What to build:** 让开发者修改本地 .env 中允许的模型运行参数后，无需重启 API 即可在下一个 turn 验证新配置，同时保证当前 turn 的行为稳定且错误配置不会被静默忽略。

**Blocked by:** 01/接通真实模型流式对话.

**Status:** resolved

- [x] development 环境在每个新 turn 边界检测 .env 是否发生变化。
- [x] 模型 API 根地址、Token、模型名、温度、模型超时、turn 超时、最大步骤和上下文消息数可以热加载。
- [x] 每个 turn 固定一个不可变配置快照，进行中的流式响应不受后续文件修改影响。
- [x] 进程环境变量优先于 .env，文件修改不能覆盖部署或 PowerShell 注入的字段。
- [x] 无效的新配置使 readiness 和之后的新 turn 明确失败，不继续静默使用最后一次有效值。
- [x] test 通过依赖注入配置且不监听文件，production 不监听 .env。
- [x] 数据库 URL、运行环境、管理 Token 和医院 Adapter 类型仍要求重启。
- [x] 日志只记录配置版本指纹、来源和变化字段名，不记录任何敏感值。

## Answer

已实现 development `.env` 运行时配置快照：新 turn 和 readiness 读取允许热加载的字段，进程环境变量保持优先，错误配置直接阻断新 turn。模型 Adapter、turn 超时、Agent 最大步骤和上下文消息数均从每个 turn 的不可变快照构建；production/test 使用启动时静态配置。配置变化日志仅输出带进程内密钥的指纹、来源及字段名，并补充了配置、Assistant 和 HTTP 边界测试与本地运行说明。
