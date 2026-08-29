# 01: 接通真实模型流式对话

**What to build:** 让就诊参与者通过现有聊天体验与后端配置的 OpenAI-compatible 模型进行真实、逐步流式的门诊就诊协助对话，同时彻底移除硬编码医院回复对正常运行的影响。

**Blocked by:** None (can start immediately).

**Status:** resolved

- [x] 配置完整的模型 API 根地址、Token 和模型名后，聊天请求通过后端模型 Adapter 返回真实 token 流。
- [x] 浏览器继续使用既有 AI SDK UI 消息流，并能正确显示开始、文本增量、结束和安全错误。
- [x] 缺少或无效模型配置时，进程 liveness 正常，readiness 和聊天明确报告不可用。
- [x] system 指令将模型限制在门诊就诊协助范围，不允许诊断、处方或把模型常识描述为服务医院事实。
- [x] 普通运行不再返回硬编码科室、医生、号源、费用、路线、proposal 或 receipt。
- [x] 上游错误、Token、内部堆栈和 Thought 不会发送给浏览器。
- [x] 自动测试使用 deterministic fake model，不访问真实或收费模型。
- [x] 既有 HTTP 流协议测试和 Web 基础聊天测试保持通过。

## Answer

已通过版本化环境配置和 ChatOpenAI Adapter 接通真实流式模型，并在 LangGraph AgentRuntime 内将显式文本块映射为现有 AI SDK UI 事件。缺失或无效配置时，存活检查保持正常，就绪检查和聊天安全失败；正常运行不再装载演示医院图或返回演示业务数据。

验证覆盖 deterministic ModelPort、mock HTTP transport、reasoning metadata 过滤、Assistant 事件顺序、HTTP SSE、Web 单元测试、lint、类型检查、生产构建，以及拦截 fake SSE 的浏览器成功与安全错误流程；自动化过程未访问真实或收费模型。
