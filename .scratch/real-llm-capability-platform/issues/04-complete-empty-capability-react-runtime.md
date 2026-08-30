# 04: 建立零业务能力的完整 ReAct Runtime

**What to build:** 让模型请求始终经过完整、受预算控制的 ReAct-capable Runtime；当业务能力集合为空时正常完成纯对话，而测试环境可以通过 dummy read Tool 证明真实 Action/Observation 循环可用。

**Blocked by:** 01/接通真实模型流式对话.

**Status:** claimed

- [ ] Runtime 在每个 turn 开始时固定 Runtime Profile、Skill 版本和 Tool 版本快照。
- [ ] LangGraph 覆盖模型调用、Tool 路由、调用验证、Observation 回流、循环预算和终态。
- [ ] 零业务 Skill、零业务 Tool 是合法状态，模型收到空 Tool 集合并正常流式完成文本回复。
- [ ] 测试专用 dummy read Tool 能被模型请求、执行，并把结构化 Observation 送回模型生成最终答案。
- [ ] 未知、停用、未绑定或越权 Tool 永远不会执行。
- [ ] 无效 Tool Call 只获得一次安全纠正机会，重复相同无效调用会触发循环终止。
- [ ] 浏览器只看到安全进度和结果，不看到内部 Schema、权限细节或 Thought。
- [ ] 测试 Tool 通过依赖注入提供，不进入开发 seed 或正常 Registry。
- [ ] Runtime 的可观察测试通过公开 Agent 事件与结果断言，不依赖私有图节点顺序。
