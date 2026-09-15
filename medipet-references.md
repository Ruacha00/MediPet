# MediPet 同类项目架构、方法与实践对标

调研日期：2026-09-15。目标：AI Agent / 大模型应用开发应届求职项目。方法：社区开发者讨论 → 10 个候选初筛 → 4 个项目的关键实现深读，另核验 Temporal harness 的执行/审批边界。没有修改 MediPet、没有运行外部项目或重跑本地评测。

## 结论

MediPet 应优先参考 **Parlant 的多轮任务推进与回退、HITL Support Agent 的中断边界和消融复盘、tau2-bench 的任务与评分设计**。它们分别提供“怎样组织对话过程”“怎样验证架构取舍”“怎样评价完整业务结果”的参考。agent-ledger 和 Temporal harness 用于研究执行恢复边界，当前不建议直接引入或替换现有运行时。

**没有找到一个能够同时证明“全面优于 MediPet、可直接迁移、真实生产验证充分”的同类项目。** “production-grade”标签、README 功能表和社区作者自述均未被当成效果证明。多个候选的确认/幂等实现反而存在与 MediPet 相同甚至更明显的边界。

本报告中的标记：

- **源码已核验**：检查了固定提交下的实际代码/测试或结果文件；不等于运行验证。
- **作者报告**：社区自述、仓库评测/Issue 的作者报告；没有独立复现。
- **分析/假设**：从机制推出的采用建议或待验证影响；不能写成已经提升项目指标。

## 一、候选池与筛选

更新时间来自当日 GitHub API 的 pushed_at；它包含仓库任意分支推送，不一定等于默认分支代码时间。主要对象另列固定提交。没有根据 star 数给质量排序。

| 候选 | 用途与筛选结果 | 最近推送 UTC / 局限 |
|---|---|---|
| [Parlant](https://github.com/emcie-co/parlant) | 主参考：面向用户的多轮任务、guideline 与 journey、回退/退出 | 2026-07-12；有较长历史、实际 Issue；完整框架替换成本高 |
| [HITL Support Agent](https://github.com/Ranjith36963/hitl-support-agent) | 主参考：SQLite 中断恢复、人工编辑/拒绝、单/多 Agent 消融 | 2026-05-25；个人项目、mock CRM，邮件集成不等于真实客服规模验证 |
| [tau2-bench](https://github.com/sierra-research/tau2-bench) | 主参考：可变业务数据库、用户模拟器、最终状态/沟通/动作的不同判据 | 2026-09-11；这是评测环境，不是现成业务系统；只用代码和 Issue 验证方法 |
| [agent-ledger](https://github.com/rune0-dev/agent-ledger) | 专项参考：操作身份、状态迁移、结果回放、stale takeover | 2026-02-08；库仍依赖下游幂等，不能许诺任意 API exactly-once |
| [Temporal Agent Harness](https://github.com/temporal-community/temporal-agent-harness) | 专项参考：持久等待、工具执行与工作流分离、审批结果回送模型 | 2026-09-15；2026-06 才建仓、API 快速变动，迁移成本高 |
| [agentic-customer-service-platform](https://github.com/negativexq/agentic-customer-service-platform) | 功能形态最接近：typed pending action、ownership/live-state、取消/退款 | 2026-09-01；2026-08 才建仓；本轮只核对 README/元数据，不能当成熟性证据，已有能力与 MediPet 高重叠 |
| [Amankhan1009/customer-support-agent](https://github.com/Amankhan1009/customer-support-agent) | 混合路由、子图、Postgres checkpoint 入门参考 | 2026-07-09；创建到末次推送约一天，缺乏长期迭代证据；本轮未深读源码 |
| [gaurav-oberoi/support-agent-hitl](https://github.com/gaurav-oberoi/support-agent-hitl) | 极简 interrupt/checkpointer 教学对照 | 2026-06-22；创建/推送只相隔数秒，订单内存存储；功能比 MediPet 少，本轮未深读源码 |
| [LikhithV02/Customer-Support-Agent](https://github.com/LikhithV02/Customer-Support-Agent) | 确定性退款规则在写入前重验 | 2026-07-19；主要价值与 MediPet 已有规则/确认重叠，README 的防攻击强结论未独立验证 |
| [OpenClaw Shopify support setup](https://gist.github.com/shashankkr9/6b2d9ae0366f8c68712552fa3596ad6d) | 作者实践复盘：收件箱轮询、处理进度、发送前重查、未完成事件再投递 | 2026-03-27 单次 gist；自称生产使用；是配置/脚本蓝图，非完整版本化服务 |

## 二、社区经验：哪些值得转化成设计问题

### 1. 持久化工作流不能替代下游幂等

[Temporal 社区：副作用在 retry/resume 时由谁保证幂等](https://www.reddit.com/r/Temporal/comments/1wdt08h/for_sideeffecting_agentworkflow_actions_who/)（2026-09-11 发帖，09-14 回复）明确讨论：外部服务已处理请求，worker 在成功写回工作流历史前崩溃，重放会再次调用。回复将去重的执行责任放在被调用服务，调用者要提供稳定操作键。该回复是机制解释，并未提供其本人真实事故记录。

对 MediPet：已有 proposal/receipt 和本地 Postgres 去重应保留。未来扩展真实外部医院接口时，另测“外部已成功、本地未记账”的窗口；加 checkpointer 不会自动解决。

### 2. 人工确认应绑定具体执行内容

[社区：Most HITL agent systems are approving a story not an action](https://www.reddit.com/r/AI_Agents/comments/1uw08nc/most_hitl_agent_systems_are_approving_a_story_not/) 有开发者描述从批准摘要改为批准真实 outgoing payload，并关联结果回执。互动量小，属于个人经验，不是行业统计。

对 MediPet：这项要求已有较好覆盖；新的问题应聚焦“改口后旧 proposal 是否失效、更新后是否给出正确新 proposal”，而非再做一个确认按钮。

### 3. 持久的“已看见”与“已处理”是不同状态

[Shopify support gist](https://gist.github.com/shashankkr9/6b2d9ae0366f8c68712552fa3596ad6d) 作者报告先保存轮询位置、处理器随后失败会吞事件，因此加入 stale_waiting 重新发出。源码脚本确实存在五分钟未处理重投逻辑。但它的发送去重只是比较最近几条回复的部分文本，不是严格事务去重。

对 MediPet 的可迁移经验是区分已接收/处理中/等待用户/完成，而不是采用该文件轮询架构。MediPet 的同步聊天请求和该项目的邮件队列不同。

## 三、主参考 1：Parlant —— 任务步骤、改口与回退

固定提交：[ea737442b8ae65854a842542e544fbe7e6144bad](https://github.com/emcie-co/parlant/tree/ea737442b8ae65854a842542e544fbe7e6144bad)，提交时间 2026-07-10，Apache-2.0。

### 源码已核验

1. `journey_path` 显式保存已走过的步骤，工具步骤和对话步骤有不同类型。
2. 活跃 journey 中，下一步选择和回退判断可并行进行；需要回退时取消下一步任务，再选择回到哪个已执行节点。
3. 回退判断的结构化输出区分“同一任务改决定/恢复原任务”和“同类任务重新开始”。

源码：

- [journey_node_selection_batch.py:286](https://github.com/emcie-co/parlant/blob/ea737442b8ae65854a842542e544fbe7e6144bad/src/parlant/core/engines/alpha/guideline_matching/generic/journey/journey_node_selection_batch.py#L286)
- [journey_backtrack_check.py:399](https://github.com/emcie-co/parlant/blob/ea737442b8ae65854a842542e544fbe7e6144bad/src/parlant/core/engines/alpha/guideline_matching/generic/journey/journey_backtrack_check.py#L399)

实际测试不只是“能回复”：出租车预订从为自己改成替儿子预约、接人点改变；点餐从三明治改成沙拉，需要跳回相关分支，而不是继续问旧分支的馅料。

- [journeys.feature:66](https://github.com/emcie-co/parlant/blob/ea737442b8ae65854a842542e544fbe7e6144bad/tests/core/stable/engines/alpha/features/baseline/journeys.feature#L66)

### 真实 Issue 带来的限制

[Issue #819](https://github.com/emcie-co/parlant/issues/819)（2026-06-23，页面仍显示 Open）报告 SDK 接受跨 journey 的非法边，持久化成功后在投影阶段 KeyError；附可复现脚本，涉及 3.3.1。本轮没有运行复现。借鉴图式任务定义时，需要验证节点归属/边合法性，不应把“有图”本身理解为可靠。

### 给 MediPet 的采用假设

先用一个小的“预约任务状态”实验：目标人/科室/日期/医生偏好、当前候选、待澄清项、当前步骤、约束来源消息。用户改日期时只失效依赖该日期的号源与旧提案；改无关称呼不必重做所有查询。上层负责判断继续/澄清/回退，底层仍沿用已有工具校验和 proposal/receipt。

**这是待验证假设，不是对 22/40 多轮失败的已定位根因。** 本轮没有逐条定位 MediPet 失败轨迹，因此不能断言引入此状态一定有效。应先确认失败发生在抽取、约束保留、工具选择、工具结果使用还是最终输出。

采用成本：借鉴小范围任务状态/回退机制为中等；替换成 Parlant 完整引擎为高。其回退判断会多调用模型；考虑 MediPet lazy 策略已有请求和延迟上升，不能直接复制整套多阶段推理。

## 四、主参考 2：HITL Support Agent —— 恢复边界与诚实的架构消融

固定提交：[d97097528961e205784cd214eab081b026394554](https://github.com/Ranjith36963/hitl-support-agent/tree/d97097528961e205784cd214eab081b026394554)，2026-05-25，MIT。

### 可借鉴的已核验机制

- 实际运行时使用 `AsyncSqliteSaver`；以 ticket_id 作为 thread_id，开始和人工恢复均经相同 graph。
- 审批节点独立，先发送审批通知再中断，避免恢复时再次执行中断前的外部副作用。
- 超过 15 分钟的审批重新读取 CRM/history/KB，比较 context hash；变化则重新展示差异并等待决定。
- 拒绝次数和发送重试都有上限，最终转人工。

源码：[graph_runner.py:54](https://github.com/Ranjith36963/hitl-support-agent/blob/d97097528961e205784cd214eab081b026394554/src/graph_runner.py#L54)、[nodes.py:489](https://github.com/Ranjith36963/hitl-support-agent/blob/d97097528961e205784cd214eab081b026394554/src/nodes.py#L489)、[nodes.py:575](https://github.com/Ranjith36963/hitl-support-agent/blob/d97097528961e205784cd214eab081b026394554/src/nodes.py#L575)。

**对标应保留差异：** MediPet 已有确认时重验，不应退化成“超过 15 分钟才重验”；其图外持久化提案也已经支持部分恢复，不能写成“没有持久化”。仅当希望恢复未完成的模型/工具循环时才研究 graph checkpoint。

### 比“多 Agent”标签更有用的经验

作者保留 v3 单代理和 v4 researcher/drafter/critic 两条路径。冻结的 27 条 Bitext 票据中：危险自动发信数量 6→1，但自动发信总量 11→2；有 5 个新过度转人工，其中 3 个是作者认定的正常 FAQ 回归。两组意图准确率同为 15/27。该比较样本小、手工映射标签、包含域外电商问题，不能推为总体安全提升。

[结果与逐票复盘](https://github.com/Ranjith36963/hitl-support-agent/blob/d97097528961e205784cd214eab081b026394554/eval/bitext27_findings.md)、[方法与局限](https://github.com/Ranjith36963/hitl-support-agent/blob/d97097528961e205784cd214eab081b026394554/eval/METHODOLOGY.md)。

对 MediPet：假设增加结果校验/critic，必须同时报告任务完成、误阻断、额外模型调用与延迟。不能只报告“危险动作减少”，也不能把一切失败统一转人工然后称成功率提升。

### 不应照搬的实现

静态源码核验发现：SMTP 发送在成功后才 `_commit_sent`，发生任意异常会释放 reservation。外部已发送但返回不确定时，重试风险仍在。另 `concurrent_in_flight` 返回空 sent_message_id，而上层把正常返回统一标成 sent。没有运行复现，故这是静态边界分析，不能称已证实线上事故。

[SMTP adapter:258](https://github.com/Ranjith36963/hitl-support-agent/blob/d97097528961e205784cd214eab081b026394554/mcp_server/support_email_write.py#L258)、[上层处理:725](https://github.com/Ranjith36963/hitl-support-agent/blob/d97097528961e205784cd214eab081b026394554/src/nodes.py#L725)。

因此它适合借鉴中断隔离、版本对照、失败复盘；不适合作为 MediPet 幂等层的替换模板。采用成本：测试与分析方法低；完整邮件/Slack/MCP 架构迁移高且业务收益有限。

## 五、主参考 3：tau2-bench —— 完整任务、不同正确路径、重复稳定性

固定提交：[2174a603f6d014ef94473ffa95957f6ce27100db](https://github.com/sierra-research/tau2-bench/tree/2174a603f6d014ef94473ffa95957f6ce27100db)，2026-09-10，MIT。技术来源仅用开源实现和开发者 Issue，不采用公司宣传或岗位建议。

### 机制已核验

工具会改变模拟业务数据；评估时以参考操作在全新环境产生目标终态，再与候选运行终态比较。`actions` 通常是一条参考路径，并非规定模型必须按完全同样顺序调用。环境、动作、沟通、自然语言断言分开，最终采用哪些由 `reward_basis` 决定。

- [evaluator_env.py](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/src/tau2/evaluator/evaluator_env.py)
- [evaluator_action.py](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/src/tau2/evaluator/evaluator_action.py)
- [当前评分说明](https://github.com/sierra-research/tau2-bench/blob/2174a603f6d014ef94473ffa95957f6ce27100db/docs/evaluation.md)

### 社区争议也要借鉴

[Issue #224](https://github.com/sierra-research/tau2-bench/issues/224) 质疑某些“不应修改”的任务只靠终态就能通过；[RFC #129](https://github.com/sierra-research/tau2-bench/issues/129) 讨论任务语义与评估。代码说明中的 airline task 1 确实允许没有任何数据库写入且没有需沟通字符串时得分；不能因此证明 agent 查证过取消条件。

### 给 MediPet 的实际启发

MediPet 已有 480 次 A 评测和独立确认可靠性评测，不需从零另建平台。值得补齐的是：

1. 成功分成最终业务状态正确、确认/约束过程正确、向用户准确交代结果；保留已有对应检查，缺哪项补哪项。
2. 正确查询顺序可能不唯一，避免把某一次成功轨迹写死成唯一答案。
3. 对拒绝/澄清任务，单看数据库“没变”不足，要判断是否基于所需信息做出合理处理。
4. 同一任务多次运行看是否每次都成功，不只统计一次平均分；将失败类型与请求次数、耗时一起分组。
5. 冻结当前用例作为开发集；改造后另保留未参与调参的表达/约束组合，防止对已知用例过拟合。

采用成本：移植判据与用例结构低到中；把医院环境接入完整 tau2 框架中到高，没有必要先做后者。

## 六、专项：agent-ledger 与 Temporal harness

### agent-ledger：学习边界，不额外套一层重复基础设施

固定提交：[c32332bc1d4c1404846df5e6e14b8b45b993d1b1](https://github.com/rune0-dev/agent-ledger/tree/c32332bc1d4c1404846df5e6e14b8b45b993d1b1)。GitHub license API 返回 NOASSERTION，但仓库 LICENSE 文本写 Apache 2.0。

核验 `begin` 对 canonical tool args+workflow 算操作键，Postgres 原子 upsert；终态直接回放结果，未完成状态等待，过期 processing 可重新 claim。

- [ledger.py:194](https://github.com/rune0-dev/agent-ledger/blob/c32332bc1d4c1404846df5e6e14b8b45b993d1b1/agent_ledger/ledger.py#L194)
- [Postgres claim:300](https://github.com/rune0-dev/agent-ledger/blob/c32332bc1d4c1404846df5e6e14b8b45b993d1b1/agent_ledger/stores/postgres.py#L300)
- [handler 后记录结果:617](https://github.com/rune0-dev/agent-ledger/blob/c32332bc1d4c1404846df5e6e14b8b45b993d1b1/agent_ledger/ledger.py#L617)

README 本身承认 exactly-once 依赖 handler/下游幂等。时间过期不意味着旧执行者绝不会再完成，因此在真实外部副作用上不能只凭超时抢占再执行。另按“工具+参数”去重必须区分两个内容相同但用户独立授权的新业务操作；MediPet 的业务操作 ID/提案身份更值得保留。

采用成本：借鉴状态模型/故障测试低；引入库会与现有 action store 重叠，中等改造却收益不明确。

### Temporal Agent Harness：持久等待与活动执行分开

固定提交：[c01a9f0e3b719528b90f649f9bde48b95799d42e](https://github.com/temporal-community/temporal-agent-harness/tree/c01a9f0e3b719528b90f649f9bde48b95799d42e)，2026-09-14 21:27 UTC；MIT。

`_apply_approval_policy` 在 workflow 内保存 tool_id/tool_input/turn_id 并 `wait_condition` 等待批准；不在实际 activity 内空等审批，避免用掉工具执行超时。拒绝变成 ToolApprovalDenied，可作为模型观察。工具 dispatch 再转成 activity。

[agent_workflow.py:302](https://github.com/temporal-community/temporal-agent-harness/blob/c01a9f0e3b719528b90f649f9bde48b95799d42e/temporal_agent_harness/harness/agent_workflow.py#L302)。最新 [PR #128](https://github.com/temporal-community/temporal-agent-harness/pull/128) 才把 OpenAI Agents SDK 的 MCP tools 接入审批与事件发送路径，说明不能仅看到框架审批功能就假定所有工具路径已覆盖。示例 ReAct 当前默认 dangerously_skip_all，也不能直接作为受控业务示范。

对 MediPet：若未来有跨数小时、跨进程恢复、外部长期任务，才有充分理由评估 Temporal。当前短轮询/预约工具与 40 例多轮质量问题不足以证明需要更换工作流基础设施。它仍不自动消除外部提交后本地未记录的窗口。

## 七、下一步对标实验：小步判因，再决定改造

下表是实验设计建议，不是实施承诺，也没有预设成功率目标。

| 已观察现象 | 可比较方案 | 判定依据 | 不能跳过的限制 |
|---|---|---|---|
| 多轮澄清 22/40、确认前约束变化 24/40 | 原 ReAct；显式任务状态；任务状态+有限回退 | 相同模型、任务、预算下：约束保留、正确候选、新提案、最终结果、调用数、延迟 | 先看失败轨迹；可能是抽取/工具/上下文等其他根因 |
| lazy 比 eager 输入 token 少但请求/耗时多 | 当前 loader；按任务阶段缓存已加载工具/证据；有限的预加载 | 全任务有效完成率、模型请求、输入输出 token、p95 | 工具只有 11 个，不能假设复杂检索路由会省钱 |
| 工具错误主要终止 | 对可恢复业务错误返回结构化 observation，给有限纠错机会 | 恢复率、重复错误、额外副作用、耗时 | read/query错误、参数错误、外部结果不确定必须分开 |
| 想增强执行恢复 | 当前 proposal/receipt；独立未完成任务状态；必要时 graph checkpoint | 在明确故障点重启，检查状态、旧提案、实际副作用数量 | checkpoint解决续跑，不解决语义理解，也不保证下游只执行一次 |
| 想增加 critic/multi-agent | 原代理；只检查关键业务结果；完整critic | 正确完成与误阻断并列，成本与延迟并列 | 小样本或换模型无法归因于架构本身 |

对求职最有价值的产出：一段可演示的改口任务、一张真实的状态演变图、一组同条件对照结果、两三个可解释的失败与恢复案例。这样能够展示借鉴、取舍和验证能力。直接复刻外部系统全部功能会扩大工程量，却未必增加该证据。

## 本地研究快照

已浅克隆到 `research/agent-benchmark-20260915/medipet-source/`，固定提交在正文列明：Parlant、HITL Support Agent、tau2-bench、agent-ledger；Temporal harness 为 sparse checkout。源码仅阅读，未执行第三方代码。`sources.json` 记录最初 raw 下载失败，后续实际核验使用成功的 git 克隆，不应把该 manifest 当成功下载清单。
