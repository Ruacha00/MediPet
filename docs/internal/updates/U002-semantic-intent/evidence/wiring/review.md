# 融合接线独立审查

审阅者：spec_agents_knowledge；日期2026-09-17。审阅当前工作树源码，不以图索引空调用证明安全。

1. 向量先完成、LLM仍在等待期间发生共享provider降级或实例learn，旧结果原本仍能独立投票。root在gather之后投票之前复核space_id/generation/template_revision；失配时本路零分弃权、不重跑LLM、不进常规缓存。两个asyncio事件协调测试覆盖generation变化和learn，342项定向检查通过。
2. 首次修复对显式disabled没有模板版本的诊断误判stale，导致缓存失效。root将复核限制为存在实际emb_task且非failed的快照；新增disabled两次同输入仅一次LLM请求的断言。101项识别器测试通过，包含上述两项时序测试及缓存修复。

记录：完整非integration套件752 passed/24 skipped，93.40秒；收集早于第2项新增测试，因此其最终修复以随后101项定向结果为证，不重复相加成测试总数。实际模型门槛与语义提升由08独立验收，机制测试不证明效果。
