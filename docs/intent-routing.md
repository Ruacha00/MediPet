# MediPet 意图识别与主辅路由

一句“查孩子明天儿科号源，再说一下要带什么”同时包含预约和准备需求。MediPet 先识别主要意图和实体，再决定负责哪些领域、是否协作，最后在同类角色实例中选择执行者。这三个步骤分别解决语言分类、业务分工和实例选择问题。

## 三路识别怎样融合

入口是 [core/intent_recognizer.py](../core/intent_recognizer.py) 的 `IntentRecognizer.recognize`。LLM 和向量模板匹配并行运行，关键词匹配同步完成。

| 分支 | 输入与计算 | 当前实现的边界 |
| --- | --- | --- |
| LLM | 消息、有限最近历史、医院类别与提示示例，解析结构化分类结果 | 无法识别的类别转为 OTHER；请求或解析失败时降级 |
| 向量模板 | 当前消息与各意图模板求余弦相似度，取最高分 | 默认使用本地BGE中文语义编码；字符n-gram哈希为显式配置的降级后端 |
| 关键词 | 先匹配具体医院意图，再尝试宽泛类别 | 覆盖明确表述；无法独立理解所有多轮语义 |

`_vote` 对每个候选意图累加“分支权重 × 分支置信分”：正常三路使用 **LLM / 向量 / 关键词 = 0.7 / 0.2 / 0.1**；关闭向量分支时使用 **0.85 / 0.15**。LLM失败时使用独立接受规则：强关键词冲突或与可独立接受的语义结果冲突时澄清；否则优先接受唯一强关键词或达标语义结果。哈希结果必须与具体关键词一致且不依赖上下文，未达要求则为`OTHER`。

宽泛类别获胜、具体关键词分数达到 0.5 且融合分低于 0.8 时，允许具体类别修正并返回；未走修正分支时，低于默认 0.5 阈值的结果转为 OTHER。融合分是路由启发式分数，没有做概率校准，不能解释成“有 80% 概率正确”。

中文语义向量由本地编码器提供，不依赖聊天SDK的embedding接口。模板与输入必须处于同一向量空间，整路降级与实际后端记录在`embedding_info`。知识库使用的Chroma向量模型属于另一条检索链路，见[工具与RAG](tools-rag.md)。

模板向量按空间身份加载后复用；模型空间、模板或校准版本变化时隔离缓存。分类缓存键包含清理后的消息、最近三条历史的截断内容及上海日期；跨零点后“明天”不会命中前一天结果。缓存达到 1000 项时删除最早插入的 500 项，命中不会移动顺序，因此不应将其描述为严格 LRU。

## 实体是线索，身份来自事项

`_extract_entities` 提取科室或医生名称、日期、上午/下午、选择序号、地点和无障碍需求。“第一个”只得到一基序号；识别器不编造 `slot_id`、预约 ID，也不凭消息替换患者。日期使用带时区的上海业务时钟，未知名称仍须服务查询核实。

API 的 `_load_visit` 与 `VisitStore.require_identity` 固定 `user_id / patient_id / conv_id`。已预分类请求和直接编排请求最终使用同一套实体与路由机制。低置信度 `OTHER` 的一般模糊消息通过 `_needs_clarification` 返回澄清，不直接调用业务工具。

当前消息明确同时要求号源与就诊材料时，`agents/task_requirements.py` 检查两项只读需求，不因主意图为低置信度OTHER而丢弃整个请求；缺少查询字段仍由对应角色澄清。显式不要/不用的分句不用于这项复合交付判定，预约准备和取消资料也不等同于就诊材料。

急症信号先经过 [core/emergency.py](../core/emergency.py) 的 `detect_emergency`。API、直接编排和独立分诊入口均复用它；有限胸痛、呼吸困难、意识异常表达及超过 39.5℃ 的演示条件优先提示立即就医，中国大陆拨打 120。一般咨询、否定、过去经历或引用不能仅因模型给出 `EMERGENCY` 标签而进入此分支；规则不代表完整医学判断。

## 医疗扩展的三个意图

健康咨询包含以下三类意图，使用共同的投票、降级和缓存机制：

| 意图 | 主领域 | 工具事实与限制 |
| --- | --- | --- |
| `SYMPTOM_QUERY` | TriageAgent | 按当前用户原文提供有限科室方向；缺年龄等信息先澄清，不生成诊断 |
| `MEDICATION_QUERY` | MedicationAgent | 只查已收录明确剂型；药名来自用户原话，未收录药或组合不推断安全 |
| `REPORT_QUERY` | TriageAgent | 粘贴文字用 `preprocess_report`；已有报告追问用 `read_current_report` 读取同一患者同一事项的最新卡片 |

共六角色：General、Guidance、Appointment、Escalation、Triage、Medication。没有独立 ReportAgent。报告文件上传走独立预处理 API，不依靠意图模型完成 OCR；上传后的自然语言追问再进入上述路由。范围见[健康咨询](health-consultation.md)。

## 领域分工与并行

[agents/agent_orchestrator.py](../agents/agent_orchestrator.py) 中，`_domain_scores` 从 General 0.1、其余领域 0 开始计分：

- 意图映射到 General 加 0.55，映射到专属领域加 0.75。
- Guidance、Appointment、Triage、Medication 每命中一个领域关键词加 0.18，最多加 0.45；General 每个加 0.12，最多加 0.35。
- 地点或无障碍实体为 Guidance 加 0.2；号源、预约或选择序号为 Appointment 加 0.15，日期或时段再加 0.1；科室或医生为 General 加 0.1。
- 明确“号源＋材料”的复合需求为 Appointment、Guidance 各加0.75，并注册两项协作目标，防止通用标签盖过已明确的业务需求。

`_route_decision` 取最高分可用领域为主角色，先采用 `_collaboration_targets` 检出的预约、指引、分诊或药品复合需求作为辅助角色。没有显式协作目标时，才考虑分数至少 0.45 且达到主角色分数 55% 的其他专属领域。人工联系和急症优先进入 Escalation。

`run_parallel` 以 `asyncio.gather(..., return_exceptions=True)` 同时进入主辅角色；每个角色有自己的本轮输出容器。`ResponseComposer.compose` 对成功文字去重整合；模型整合失败时按主次拼接已有文字。卡片与工具轨迹独立汇总，失败分支和卡片冲突保留证据。并行分工并不保证每次都更快，尚无相同条件的性能对照。

角色确定后，`_best_agent` 才在同类实例中取最高 `routing_score()`：

```text
(成功率 × 0.7 + 1 / (1 + 平均毫秒 / 1000) × 0.3)
× max(0, 1 - 监控惩罚)
```

这可以影响同类实例的选择，不能让一个领域实例代替另一领域的权限。专属角色失败时可尝试 General 降级，同时保留前序工具与失败轨迹。

## 证据与取舍

[test_intent_recognizer.py](../tests/test_intent_recognizer.py) 覆盖两套权重、失败优先级、阈值、实体、跨日缓存、字符向量及急症反例。[test_agent_orchestrator.py](../tests/test_agent_orchestrator.py) 覆盖领域分数、主辅选择、并行进入、同类性能路由和失败结果保留；[API 闭环](../tests/test_demo_workflow.py) 验证真实工具链中的号源与材料协作。这些使用模型替身，证明机制按约定执行。

新增角色、医疗意图及工具接线检查见 [test_health_routing.py](../tests/test_health_routing.py)，当前用例见[评测集](../evaluation/cases/intents.json)。三路线索不能解决所有自然语言歧义，哈希降级不等于语义编码。评测结果及LLM故障场景的已知限制见[测试与评测](evaluation.md)。
