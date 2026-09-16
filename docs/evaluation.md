# MediPet 评测、监控与证据

MediPet 分别检查“程序是否正确改变业务状态”和“模型是否理解并回答得好”。库存、患者归属、预约与回执由确定性断言验证；LLM Judge 只评回答质量，不能凭一段流畅文字判定预约已经发生。

截至 2026-09-16，医疗扩展前的两份完整真实模型候选已保存，另有确定性 API、真实存储、浏览器和容器证据。首份因业务失败及评测机制缺口未接受；第二份旧版容器报告仍为候选。U001 已扩展下面的输入，医疗能力的实际验证另见 [U001 检查点](internal/updates/U001-health-consultation/EXECUTION.md)。各层测试数量不相加成模型质量率，也没有可声称的优化提升幅度。

## 数据与执行路径

U001 当前证据：[完整候选](../evaluation/reports/live-20260916-u001/notes.md)单次66/15/12输入，66/66意图、30/30质量项、26/27业务组，合计57/58结果项；新医疗场景通过，唯一失败为旧无障碍路线续问未查询。Guidance提示定点修复后的[原样两轮复测](../evaluation/reports/live-20260916-u001-route-fix/notes.md)3/3通过；没有把局部复测改写成完整58/58。后端684 passed/21 skipped、前端35/35、真实OCR与浏览器证据见[验收汇总](internal/updates/U001-health-consultation/evidence/acceptance.md)。

| 数据 | 规模 | 检查内容 |
| --- | --- | --- |
| [intents.json](../evaluation/cases/intents.json) | 66 条 | 原54条加症状、用药、报告各4条，独立于提示示例 |
| [dialogs.json](../evaluation/cases/dialogs.json) | 15 组多轮 | 原12组加分诊、药品组合、报告原文比较各1组 |
| [boundaries.json](../evaluation/cases/boundaries.json) | 12 组 | 重复确认、过期、旧方案、身份冲突、末号竞争、检索失败与急症等 |

[evaluation/evaluator.py](../evaluation/evaluator.py) 的 `EndToEndEvaluator.run` 使用 `case_runtime_factory(case, run_id)` 为每个业务场景取得依赖，固定业务时钟并建立独立事项。`_turn` 实际构造带患者身份的 `Request`，调用编排器并写入历史；这是直接编排与服务评测，不是 HTTP 测试。HTTP 确认契约另由 [test_chat_api.py](../tests/test_chat_api.py) 和 [test_demo_workflow.py](../tests/test_demo_workflow.py) 覆盖。

`setup`、以轮次索引指定的 `between_turns`、`after_turns` 表示明确的界面动作，例如准备已有预约、切换患者、点击确认和恢复事项。`_action` 执行这些动作，它们不会被转换成让模型“自行确认”的聊天指令。六个无需聊天的事务边界由固定场景处理执行真实医院服务；其余场景从实际工具轨迹、角色、卡片和存储快照取事实。

[api/evaluation_runtime.py](../api/evaluation_runtime.py) 的应用工厂为每个场景生成 UUID Redis 前缀和两份 Chroma 记忆集合，创建独立编排器、患者数据、服务及工作记忆；静态知识查询可复用。退出后只扫描自身 Redis 前缀、删除自身两份集合并关闭独立模型客户端，不清空演示数据库。指定故障场景注入检索失败或模型不可用，SDK 调用包装器记录本场景实际调用次数。

## 指标如何计算

`IntentEvaluator` 根据实际预测计算 Accuracy 与按标签计算的 Macro-F1；分类调用失败记为缺失预测。它衡量标签预测，不自动证明下游工具或业务行为成功。

`LLMJudge.judge` 返回相关性、准确性、完整性、有用性四项 0—1 分数，单轮 overall 为四项算术平均，通过阈值为 0.75。评测器校验字段完整、数值有限且范围合法；调用失败、无效 JSON、缺字段、越界或非有限值进入 `judge_failures`，不混入质量平均值。编排轨迹中的模型或整合失败保留 `call_failed`，不会凭降级文本取得有效质量分；当前代码先检查失败，再处理不评分的 `judge=False` 边界轮。

首份候选完成后，`_turn` 的 Judge 背景已补为 JSON：`prior_context` 保留本轮开始前的记忆，`current_turn` 包含实际绑定身份、业务时钟、本轮 artifacts、工具名与完整 trace；同一字符串保存到结果的 `metadata.judge_context`，便于核对实际输入。没有把场景预期断言、未来界面动作或预期分数发送给 Judge，也不以重新读取本轮回答充当事实背景。卡片保留实际业务数据，检索 trace 提供实际来源标识；这不表示包含所有检索片段全文。此处说明当前源码，不能据此改写首份候选的分数或宣称质量提升。

业务断言从 `_snapshot` 与 `_business_facts` 获得实际状态，例如预约条数、库存变化、当前方案、号源选择、回执、工具来源和模型调用数。协作断言要求同一复合轮的角色与卡片；患者切换先要求源患者确有选择和待确认方案，并有显式儿童已有预约夹具，避免“始终什么都没做”也通过。当前来源计数只读取成功检索 trace 的 `sources`，条目须有非空来源标识；不再用返回条目数 `result_count` 代替来源证据。

未知断言记为未验证，缺少隔离、时钟或故障夹具的场景标为 skipped，都不算通过。Judge 给高分不能覆盖业务失败；报告单列 `business_failures`，有失败或跳过时不会显示“所有已测指标均达标”。

报告 `total/passed/pass_rate` 统计的是生成的结果项，可能包含意图汇总、各轮质量及业务断言结果；它不是当前93个输入场景的统一模型准确率（旧版为78个输入）。读取结果时应同时看 `case_counts`、有效质量样本数、各失败列表、具体断言及 trace。

## 候选报告与接受基线

普通运行仅写 `candidates/<run_id>.json`，包含时间、模型、Judge 参数、输入 SHA-256、样本数、实际响应、轨迹、卡片、失败与跳过。`run` 不会覆盖已接受基线。

只有显式调用 `accept_baseline(report, reviewed_by=...)` 才将人工复核过的报告标为 accepted 并保存。读取基线要求同时存在复核人和 accepted 标记，旧式自动保存报告或前一次候选不会自动成为比较对象。现有比较器对相同指标检查相对下降超过 5% 的情况；解释回归前仍需核对模型、参数、数据集和执行版本是否一致。

真实模型报告保留完整失败样本、当前代码版本、实际模型/参数和运行环境，再经人工查看业务断言与回答。首份候选的原始 JSON、截图和分数已经保存，后续修正使用新的运行编号和目录，不覆盖旧结果。

本次模型配置为 `deepseek-flash`，使用 Anthropic 兼容 SDK。[core/llm_utils.py](../core/llm_utils.py) 的 `llm_request_options` 支持 `MEDIPET_THINKING=disabled`，通过 SDK `extra_body` 显式关闭 thinking；未设置时保留服务默认值，其他非空值报配置错误。九处模型请求统一读取该选项，原 token 预算、温度、三次工具循环和投票规则不变。首份正式运行已使用 disabled；Judge 为 `temperature=0.0 / max_tokens=256`，下一份报告继续记录实际设置。

[短 JSON 实测](internal/rebuild/evidence/deepseek-thinking.md)用同一改写 prompt 做两次调用：默认配置耗尽 256 输出 token，只返回 thinking 块，没有可解析正文；disabled 返回有效三项 JSON，输出 33 token。这个对照只解释该短 JSON 故障及配置选择，不能推导全评测准确率、所有请求延迟或整体成本改善。

## 首份真实候选与限制

[首份候选说明](../evaluation/reports/live-20260916-full/notes.md)记录运行 `0f457b4d311941aab9ad63a16dad216d`。一次页面请求经开发代理 5173→8010 完成，HTTP 200，耗时 **269.062 秒**；页面错误 0，62 个运行源文件前后指纹相同。完整输入仍为 54 条意图、12 组多轮、12 组边界。

| 项目 | 本轮原始结果 |
| --- | --- |
| 意图分类 | **54/54**，Accuracy 与 Macro-F1 均为 1.0000，仅适用于本次固定输入 |
| 有效质量项 | **24 项，9 项低于 0.75**；Judge 未返回评分理由 |
| 业务断言组 | **24 组，`collaboration` 未通过**；原记录其余 23 组通过，但受下述机制缺口限制 |
| 汇总结果项 | 39/49 通过，`pass_rate=0.7959`；不是 78 个输入的准确率 |
| 原始异常汇总 | Judge 失败、调用失败、跳过均为 0；调用失败零值不能排除当时的漏报 |

协作场景只返回 Appointment 与号源卡，缺少要求的 Guidance 角色和材料卡，虽然回答包含材料文字，仍是实际业务断言失败。另一个低质量项中，“大厅到药房”返回地点 `not_found`。完整十项未通过结果、四维均值及原文保存在上述候选说明与 [response.json](../evaluation/reports/live-20260916-full/response.json)，不删除失败或追改分数。

本轮审查确认三个评测限制：不评分边界轮的提前返回可能漏报模型/整合失败；来源断言误用检索条目数，可能放过无真实来源的结果；Judge 只收到本轮开始前的记忆，没有本轮卡片、工具事实与业务时钟。9 个低分都在首轮，8 个有成功号源/方案事实，但报告没有评分理由，**不能断言上下文缺口就是低分原因**。当前代码已补失败检查、真实来源计数和 Judge 背景输入，数据格式、四维规则及模型参数不变；修正后的实际表现仍需新报告。

实测 269.062 秒超过当时容器 Nginx 的 180 秒等待。[nginx.conf](../frontend/nginx.conf) 已为 `/api/python/eval/run` 单独设置 1800 秒，其余代理保留 180 秒。重建后的容器页面完成了下一份完整报告，HTTP200耗时170.344秒；这是新代理完整请求通过的证据，该次本身未持续超过180秒。

## 当前完整容器候选

[运行说明与完整原文](../evaluation/reports/live-20260916-container/notes.md)：`a13acc91e787406bb909067a456be4a3`，真实 deepseek-flash/disabled，54条意图+12组多轮+12组边界，62个运行源文件前后指纹一致、一次页面请求、无页面错误。容器候选文件与HTTP报告规范JSON的SHA-256相同，运行前后接受基线均不存在。

- 意图54/54，Accuracy与Macro-F1均1.0；这是固定54条样本的结果。
- 24/24业务断言组、24/24有效质量项通过；加上意图汇总共49/49结果项，不是78条输入的统一准确率。
- 四维均分：相关性0.9917、准确性1.0、完整性0.9583、有用性0.9604；调用异常、Judge失败、跳过、业务断言失败均0。
- `archive-restore` 中模型仍把“大厅”原样传入地点工具，得到not_found并要求澄清；归档状态断言与诚实答复质量通过。固定完整地点的无障碍指引另有通过证据；不把49/49解释成所有工具调用成功。

报告保持candidate，等待人工复核。两次实现与Judge背景存在差异，不把分数或耗时变化写成同条件优化提升。

## 医疗扩展前的证据

| 层次 | 实际结果 | 能证明什么、不能替代什么 |
| --- | --- | --- |
| 确定性 API 闭环 | **24 passed**，[I04 记录](internal/rebuild/specs/S06-api/issues/I04.md) | 真实 API/工具/服务，模型与存储替身；证明契约和状态链路，不证明真实模型表现或网络服务运行 |
| 评测与 API 机制 | 修正后 **34 passed**，[V02 记录](internal/rebuild/specs/S08-delivery/issues/V02.md) | 替身分类器/Judge/工具计划消费全部 78 个输入样本；证明失败计分、真实来源计数、Judge 背景、隔离、断言与基线保护 |
| 真实 Redis/Chroma | **18 项真实检查通过**，[存储证据](internal/rebuild/evidence/storage.md) | 实际事务竞争、过滤、TTL、来源与重新初始化；摘要/画像模型仍用替身 |
| 最终后端回归 | **389 passed，21 skipped**，[JUnit原始结果](internal/rebuild/evidence/final-tests.xml) | 仅对应c712e87旧基准；21 项需显式真实环境，跳过不算通过。真实组件由独立证据说明 |
| 前端逻辑与构建 | **32/32 通过**，生产构建通过，[U05 记录](internal/rebuild/specs/S07-ui/issues/U05.md) | Vue 挂载与受控 HTTP 验证展示、错误和隔离，不当作真实模型质量 |
| 实际浏览器 | 业务六组、公开信息/导诊/急症三组断言通过；管理交互通过，[业务证据](internal/rebuild/evidence/browser.md)、[管理证据](internal/rebuild/evidence/management.md) | 使用真实模型、Redis、Chroma；预约取消、刷新/归档、来源与 Skill 重载已运行。零样本真实报告和受控失败报告仅证明接线/展示 |
| 容器与重置 | 四服务重建持久化、同源代理、初始化幂等和精确重置通过；重置另 **4 项替身测试通过**，[容器证据](internal/rebuild/evidence/runtime.md) | 独立 `medipet-validation` 项目，保留外来键/集合及评测文件；该验收不调用模型，不包含完整评测长请求 |
| 完整真实模型质量 | 两份报告已保存；当前容器候选49/49结果通过、等待人工接受 | 固定样本数字、实际地点澄清及比较限制见上文，不声称提升 |

真实存储那次命令原始汇总为 `19 passed, 1 skipped, 29 deselected`：其中一项内存替身因名称含 `real_list` 被筛中，已从真实通过数剔除。真实参数下的归档竞态注入明确跳过，不算通过。各测试集合存在重叠，389、32、24、34、4 等数量不相加为总样本数或质量率。测试证据说明的是当时工作树实现，后续代码改变应按受影响路径重新验证。

可在已配置依赖的工作树中复核确定性检查：

```powershell
python -m pytest tests/test_chat_api.py tests/test_demo_workflow.py -q
python -m pytest tests/test_evaluation.py tests/test_evaluation_api.py -q
```

真实存储需要显式隔离测试配置，具体步骤和清理范围见上述存储证据。浏览器交互、容器持久化和模型候选已各自保存独立报告；这些证据相互补充，不能用测试数量替代另一层验收。

## 运行监控的解释范围

[monitor/performance_monitor.py](../monitor/performance_monitor.py) 收集 Agent 成功率、延迟、工具统计和状态，监控惩罚反馈给同类实例的 `routing_score`；[api/main.py](../api/main.py) 提供 `/monitor`、`/trace/tools`、`/trace/tool/{request_id}` 与 `/metrics`。运行轨迹有助于解释本次用了哪些角色、工具和检索，但没有工具调用的请求不能凭名字宣称经过 RAG。

统计依赖实际经过的组件：MCP 工具统计主要反映静态检索，预约事务应看业务回执和服务测试。当前指标用于观察与回归定位，没有受控数据证明动态路由、并行或重排带来特定百分比的改善。
