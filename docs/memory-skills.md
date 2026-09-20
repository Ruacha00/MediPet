# MediPet 记忆、患者范围与 Skills

连续对话需要记住“明天儿科”和“选第一个”，页面刷新需要找回全部消息；为孩子办理的事项又不能混入本人的预约。MediPet 用完整历史保存产品事实，用有限模型上下文帮助理解，并在两条链路中传递同一患者身份。

## 完整历史与模型上下文

| 数据 | 保存内容 | 生命周期与入口 |
| --- | --- | --- |
| 完整事项历史 | 用户与助手消息、卡片、运行 metadata、确认事件和操作回执 | `VisitStore.append_messages/get_messages`，Redis 持久记录，不随压缩删除 |
| 报告派生记录 | 上传文件名、提取文字、数值及参考范围卡片 | 保存在同一事项完整历史；原始 PDF/图片不保留 |
| 最近选择 | 本事项最近号源列表、查询条件、状态、当前方案 ID | `VisitStore.get_selection/save_selection`，用于序号选择，不能由摘要替代 |
| 工作窗口与摘要 | 最近对话与较早内容的压缩摘要 | `MemoryManager.add_message/get_context`，Redis 24 小时 TTL |
| 情景记忆 | 压缩片段的摘要及身份 metadata | Chroma `medipet_episodic`，按当前事项优先召回 |
| 患者资料、参与者偏好 | 明确的患者表达与语言/回答长短等偏好 | Chroma `medipet_profiles`，分开作用域和稳定文档 ID |

源码入口分别为 [memory/visit_store.py](../memory/visit_store.py) 和 [memory/conversation_memory.py](../memory/conversation_memory.py)。`VisitIdentity` 包含 `user_id / patient_id / conv_id`；读取上下文前仍用 `require_identity` 校验当前事项。一个事项固定属于一位患者，切换患者应切换或新建事项。

`MemoryContext.to_prompt_text` 将摘要、最近消息、情景片段、资料和选择状态组合成背景文本。它可以帮助理解“第一个”，但最后选哪条号源由真实 `SelectionState` 校验；剩余库存和预约状态必须重新查服务，不能以历史回答为事实。

报告追问也有独立事实入口：Triage 的 `read_current_report` 重新校验当前身份，只读取当前事项最新 `report_summary`。即使同一患者的其他事项曾有报告，此工具也不回退查找；无报告时提示上传或粘贴。普通情景记忆可能回退到同患者其他事项，因此不能把记忆摘要当作当前报告数值。见[健康咨询](health-consultation.md)。

## 压缩、过期和恢复

每次 `add_message` 写 Redis 窗口并续期至 86400 秒。达到 15 条触发 `_compress`：对除最近五条外的内容生成摘要、合并已有摘要、存入情景集合，工作窗口只保留最近五条。摘要合并受 800 字符目标约束，模型失败有有限文本兜底；失败并不代表完整历史消失。

窗口过期或确认后失效时，`_restore_working_memory` 从当前事项完整消息取最近最多 20 条重建，必要时再次压缩。恢复携带消息 ID、卡片和 metadata，不重放原先的预约操作。窗口恢复使用 WATCH，遇到别人已恢复/写入的窗口就读取现状，不循环覆盖。

`_search_episodic` 先过滤参与者、患者、当前事项，不足五条时才回退到同一患者的其他事项，再去重截取。它不会只按参与者召回所有家属。Chroma 不可用时情景召回返回空列表，当前事项的 Redis 历史仍是另一份数据来源；这不等于长期记忆功能在故障时仍完整可用。

归档通过 `VisitStore.update_visit` 改变元数据，不删除消息或预约。归档事项不能继续聊天或确认，恢复后才能继续；完整消息查询用于页面历史展示。

## 确认和急症怎样接入记忆

[api/main.py](../api/main.py) 的聊天入口先写完整消息，再更新工作窗口；普通路径还安排画像更新。确认入口直接从医院服务取得回执，用稳定的 `confirm:{receipt_id}`、`result:{receipt_id}` 保存两条消息，再调用 `invalidate_working_memory`。下次聊天从完整历史恢复，能够看到真实执行回执；重试不会生成重复消息或再次扣库存。

急症路径跳过普通 `get_context`，固定响应仍写完整历史和窗口，但两次 `add_message` 都使用 `compress=False`，也不安排画像更新。因此即使窗口正好到压缩阈值，急症响应也不必等待模型。这里保证的是预设明确急症演示路径，不是医疗诊断。

报告上传预处理不请求模型，也不安排画像更新。成功报告以用户上传消息和助手卡片写入所选患者、事项，工作窗口失效后由普通聊天恢复；上传接口不更改最近号源选择或执行预约。原文件不保留并不等于报告内容不保存，提取文字与卡片会随完整历史持久保存。

`update_profile` 只看最近用户原句，要求模型分别返回 `patient_facts` 与 `preferences`。程序再次检查建议值确实是用户原句的子串；患者资料按参与者与患者保存，表达偏好按参与者保存。此约束减少无依据的合成信息，但不是语义正确性证明，历史陈述也不能代替当前医院记录。

## Skills 如何影响下一次请求

[core/skill_loader.py](../core/skill_loader.py) 的 `SkillManager` 读取 [skills](../skills)，支持 Markdown、文本和 JSON。`enabled` 控制启用，`agents` 限定角色，`keywords` 做不区分大小写的包含匹配；空关键词表示对应角色常驻。匹配内容进入角色 system prompt，单 Skill 正文默认约 3200 字符，总正文预算默认 5000 字符，因此文件应把关键边界放前面。

| 当前 Skill | 注入范围 | 解决的问题 |
| --- | --- | --- |
| [general_visit](../skills/general_visit/SKILL.md) | General，医院相关关键词 | 公开事实查询与澄清 |
| [appointment_assistance](../skills/appointment_assistance/SKILL.md) | Appointment 常驻 | 使用真实列表，准备与确认分开 |
| [visit_guidance](../skills/visit_guidance/SKILL.md) | Guidance 常驻 | 材料、流程和预置文字指引 |
| [health_triage](../skills/health_triage/SKILL.md) | Triage 常驻 | 有限科室建议、缺信息澄清、报告原文与范围边界 |
| [medication_information](../skills/medication_information/SKILL.md) | Medication 常驻 | 指定标签、未知药/组合、个人用药与处方边界 |
| [service_boundaries](../skills/service_boundaries/SKILL.md) | 六角色常驻 | 不替代医生诊断、不开处方、不评论其他医院或医生方案；急症、身份与确认边界 |

当前共 **6 组 Skills**。表中角色范围描述加载配置：普通角色构建 prompt 时注入匹配规则，Escalation 则直接返回固定响应，不读取 Skill prompt，修改并重载规则不会改变它的固定内容。报告由 Triage 处理，不新增 ReportAgent；两个医疗 Skill 与全局边界共同约束普通角色输出，个体剂量、儿童、孕哺期和肝肾异常交医师或药师核对。症状建议与药品资料只覆盖[已收录范围](health-consultation.md)，提示词不是医学有效性或模型遵守保证。

文件修改不会自动进入已加载对象。`GET /skills` 查看已加载内容摘要和解析错误，`POST /skills/reload` 重新扫描并更新编排器，后续请求使用新内容。单个文件解析失败会记录错误，其他文件仍可加载。Skill 是提示规则，不改变工具白名单、身份权限或预约事务校验。

## 验证与边界

[test_visit_memory.py](../tests/test_visit_memory.py)和[test_conversation_memory.py](../tests/test_conversation_memory.py)覆盖身份过滤、工作窗口与完整历史恢复；[Skill测试](../tests/test_knowledge_skills.py)检查修改规则与显式重载后的行为。真实组件检查需要独立测试存储，运行方法见[评测说明](evaluation.md)。

这种设计保留可展示、可核查的完整记录，同时限制模型上下文长度。代价是摘要和召回可能遗漏信息，Chroma 与 Redis 也没有共同事务；关键业务判断因此始终回到患者范围内的医院服务，完整历史不会被宣传成每轮全部送入模型。
