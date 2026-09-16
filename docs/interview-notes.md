# MediPet 设计与面试说明

MediPet 把门诊对话连接到可验证的业务操作：用户为本人或家属建立事项，模型选择查询工具、整理号源与材料，用户明确确认后由业务服务执行预约或取消。展示重点是从一句话到工具结果、卡片、事务回执和历史的完整链路。

当前已有确定性接口、机制测试、真实存储、真实模型浏览器闭环、容器演练及两份全量模型报告。首份保留失败与审查限制；修正后的完整容器报告已完成，仍等待人工复核接受。下面的机制描述来自当前源码，不能用测试通过数替代真实模型质量，也不声称未经同条件对照测量的提升。

## 从一次请求讲起

[api/main.py](../api/main.py) 的 `chat` 先加载事项绑定的患者，再检查固定急症信号。普通请求恢复有限上下文，完成意图识别和主辅分工；各角色只调用自己的工具，结果中的卡片和 trace 独立保存。最终文字、卡片及运行记录写入完整事项历史。

确认预约是另一条请求：`confirm_appointment` 从服务器重新读取身份和方案，调用事务服务，保存原始回执及确认消息，并使工作窗口失效。后续对话从完整历史恢复，能看到真实执行结果。详情见[架构](architecture.md)与[预约闭环](appointments.md)。

## 设计问题、入口与取舍

| 问题 | 实现入口与行为 | 可复核证据及取舍 |
| --- | --- | --- |
| 一句话可能既查号又问材料 | [IntentRecognizer](../core/intent_recognizer.py) 三路投票；[AgentOrchestrator](../agents/agent_orchestrator.py) 的 `_domain_scores/_route_decision/run_parallel` 负责领域分工及并行 | [路由说明](intent-routing.md)、`tests/test_intent_recognizer.py` 与 `tests/test_agent_orchestrator.py`；规则可解释，但不能覆盖全部语言歧义 |
| 模型可能提出越权或无效工具调用 | `BaseAgent._call_llm` 校验白名单和参数，单角色最多三次模型请求；[agents/tools.py](../agents/tools.py) 注入服务端身份 | Agent 工具测试覆盖非法参数、权限、失败与轮数上限；有限循环可能提前终止复杂请求 |
| 如何证明卡片是真的 | `AgentTurn` 从实际工具输出校验并收集 `artifacts`，文字整合不生成业务事实；检索 trace 只复制实际来源字段 | [工具说明](tools-rag.md)及 API 闭环测试；文字与结构化结果仍可能不一致，展示时以卡片及回执核对 |
| 预约并发和重试怎样处理 | [HospitalService.confirm_proposal](../hospital/service.py) 使用 Redis WATCH/MULTI/EXEC；已执行方案返回原回执，取消仅恢复一次库存 | [预约说明](appointments.md)中的真实 Redis 竞争证据；事务冲突显式返回，跨存储和后续历史写入不在同一事务中 |
| 为什么准备和确认分开 | 模型只能准备方案；固定确认接口检查患者、事项、当前方案、有效期、库存及快照 | [API 闭环测试](../tests/test_demo_workflow.py)；准备不预占库存，用户确认时仍可能无号 |
| 孩子的上下文为何不会变成本人的业务身份 | [VisitStore](../memory/visit_store.py) 固定 `VisitIdentity`，工具不接受身份覆盖；[MemoryManager](../memory/conversation_memory.py) 对情景和资料按患者过滤 | [记忆说明](memory-skills.md)、API 身份冲突及真实 Chroma 过滤检查；预置参与者属于演示机制，不是登录认证系统 |
| 完整记录与上下文长度如何兼顾 | 完整消息持久保存；工作窗口 24 小时 TTL，达到 15 条压缩并保留最近 5 条，失效后恢复有限窗口 | 真实 TTL/恢复检查及 [test_conversation_memory.py](../tests/test_conversation_memory.py)；摘要和召回可能遗漏信息，关键状态仍读业务服务 |
| 动态号源为什么不进入 RAG 缓存 | 医院工具直连服务；[MCPToolManager](../mcp/tool_manager.py) 只为静态知识做改写、并行召回、去重和重排 | [RAG 说明](tools-rag.md)；增加模型请求与失败点，目前没有受控提升结论 |
| 场景规则怎样调整 | [SkillManager](../core/skill_loader.py) 按角色和关键词注入提示，显式 reload 后用于后续请求 | [test_knowledge_skills.py](../tests/test_knowledge_skills.py)；提示规则不能替代工具权限或保证模型服从 |
| 如何衡量“成功” | [EndToEndEvaluator](../evaluation/evaluator.py) 分开意图指标、四维 Judge 和实际业务断言；[隔离工厂](../api/evaluation_runtime.py) 为场景建立独立数据空间 | [评测说明](evaluation.md)和[首份候选](../evaluation/reports/live-20260916-full/notes.md)；失败与审查限制保留，候选不自动成为基线 |

## 可以展开回答的细节

### 意图识别与角色路由解决不同问题

三路正常权重为 LLM 0.7、向量 0.2、关键词 0.1，先得到主要意图。领域分数再叠加实体和明确关键词，确定主辅角色；同类实例的成功率、延迟及监控惩罚只影响该领域内的实例选择。一次复合请求会增加参与角色及模型请求，不能仅因用了并行就宣称更快。

当前意图向量是本地字符 n-gram 哈希，不是已接入的远程语义向量服务。静态知识的向量来自 Chroma 客户端默认模型，这两条链路须分别说明。纯“准备预约/准备取消”只需要预约角色，“查号并问材料”才需要明确协作；关键词边界由正反例回归验证。

### 工具管理与知识来源按真实调用解释

`mcp/` 是进程内工具管理代码，没有部署独立 MCP 协议服务器。角色决定是否调用 `search_knowledge_base`，并非每条聊天都先检索。知识改写失败可使用原问题；部分召回失败保留成功片段及错误；重排失败保留原顺序。工具 trace 的来源复制实际返回的标题、来源、文档和片段标识，不从模型正文推测来源。

静态文档可以缓存；当前号源、患者预约与确认必须读取业务服务。这样可以直接检查每次状态变化，但没有覆盖真实医院履约、多医院协议或支付。

### 事务成功与历史保存分开恢复

确认事务提交后，历史写入仍可能失败。重试同一方案取得原回执，再补齐稳定消息 ID 对应的确认事件和结果；不会因此再次扣库存。创建回执保留当时的预约快照，即使后来取消也不篡改它；查当前状态需重新查询预约。

这是一种明确的业务事务边界与重试补齐方式，不是 Redis 与 Chroma 的分布式事务。完整历史用于展示与恢复，不能把每轮有限上下文说成“模型始终记住全部历史”。

### DeepSeek 接入保持现有 SDK

模型配置为 `deepseek-flash`，请求使用 Anthropic SDK 的 Messages 接口与 DeepSeek 兼容地址。没有新增供应商框架。[llm_request_options](../core/llm_utils.py) 将 `MEDIPET_THINKING=disabled` 转为 `extra_body={"thinking":{"type":"disabled"}}`，九处既有模型请求统一消费，原投票权重、提示、输出预算和工具循环上限保持不变。

短 JSON 对照使用同一查询改写 prompt 和 256 token 预算，默认配置只返回 thinking 块并耗尽预算，禁用后返回可解析的三项 JSON。它解释一次短输出故障，不能推广成完整质量或成本提升。未设置该变量时保留服务默认行为；接口能力见 [DeepSeek Anthropic 兼容说明](https://api-docs.deepseek.com/guides/anthropic_api/)和 [Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)。

### 怎样避免评测给出漂亮但无效的结论

54 条意图、12 组多轮、12 组边界各有独立作用。意图报告计算 Accuracy 和 Macro-F1；Judge 检查相关性、准确性、完整性、有用性；预约数量、库存、归属、回执和同轮协作由实际状态断言。明确的界面确认、切换患者、过期等场景动作由评测器执行，不让模型自行确认。

每个业务场景用独立 Redis 前缀和 Chroma 记忆集合，固定业务时钟并在退出时清理。Judge 无效 JSON、调用失败、跳过和业务断言失败分别保留，高质量分不能覆盖业务失败。页面结果项通过率也不能直接说成“78 条样本准确率”。正式基线需要人工复核完整报告与失败案例；当前尚待这一阶段完成。

独立审查促成三项可定位修正：不打 Judge 分的边界轮也要记录执行失败；来源断言检查实际 `trace.sources` 标识，不能用检索条目数替代；Judge 背景包含旧上下文及本轮实际卡片、工具成败、来源、绑定患者/事项和业务时钟，并将输入保存在 `metadata.judge_context`。预期断言、未来动作和预期分数不传给 Judge，四维评分规则与阈值保持不变。这些确定性修正不证明首份低分的原因，也不证明后续模型质量提高。

## 展示证据的顺序

先按[演示脚本](demo-script.md)展示实际号源、待确认方案、确认回执与取消结果，再展开对应请求的主辅角色、工具输入、结果和来源。随后解释确定性测试证明机制，真实存储测试证明事务与过滤，实际模型报告衡量当前配置下的语言表现。

容器启动/重建、定向重置及外来数据保留见[运行证据](internal/rebuild/evidence/runtime.md)，命令见[README](../README.md)。2026-09-16 本轮完整后端回归为389 passed、21 skipped，前端32/32及构建通过；真实组件另有独立运行证据，不能把这些子套数字相加。

[首份完整候选](../evaluation/reports/live-20260916-full/notes.md)运行编号为 `0f457b4d311941aab9ad63a16dad216d`，原始失败分数保持原样。[完整容器候选](../evaluation/reports/live-20260916-container/notes.md)为 `a13acc91e787406bb909067a456be4a3`：54/54意图、24/24业务断言、24/24质量项通过，无调用/Judge异常或跳过；HTTP200耗时170.344秒，仍等待人工接受。“大厅”简称的工具not_found与诚实澄清保留，结果项全通过不等于所有工具都成功。不同实现、不同 Judge 背景的两次运行不能当作同条件性能对照。
