# U001 评测输入准备与确定性验证

2026-09-16；提供给 [U001-13](../issues/U001-13.md) 的输入准备记录。**没有执行真实模型评测，不关闭 U001-13，不接受任何候选基线。**

## 输入范围

| 文件 | 本次内容 | 验证 |
| --- | --- | --- |
| [intents.json](../../../../../evaluation/cases/intents.json) | 原 54 条 + symptom_query / medication_query / report_query 各 4 条，共 66 条 | 新句与当前分类 `_TEMPLATES` 无逐字重复；全部消息/ID 唯一、意图和角色标签有效 |
| [dialogs.json](../../../../../evaluation/cases/dialogs.json) | 原 12 组 + 3 组两轮，共 15 组 | 成人 36 岁症状与补充资料；布洛芬200mg普通片与华法林标签核对；多行合成报告与缺失范围 |
| [boundaries.json](../../../../../evaluation/cases/boundaries.json) | 原 12 组不变 | 与基准一致 |

使用只读 `git show c712e87:evaluation/cases/<文件>` 解析基准 JSON，与当前文件前缀逐项比较：原 54 条意图、12 组对话、12 组边界完整保留。新增用例使用当前枚举和实际工具输入，不依据真实模型得分改写。输入规模为 **66 意图、27 业务场景、30 对话 Judge**；这些数量不是通过率。

新增业务断言仅用现有 `agents.include`、`has_artifact`，从实际轮次的角色与卡片取得事实；没有新增 evaluator 业务事实、医疗规则或自动确认动作。断言只能证明角色及卡片出现，不验证医学内容充分性、具体科室选择或数值正确性；这些内容由健康服务和接口测试负责。报告原文为合成数据，没有真实个人医疗信息。

## 替身测试

[tests/test_evaluation.py](../../../../../tests/test_evaluation.py) 的 `ScenarioChat` 仍以固定工具计划替代模型决策；新增三组调用真实 `build_health_tools` 的处理函数，不根据预期断言制造卡片。同步/异步处理函数均按真实返回值执行。分类器、Judge、Redis、Chroma 为替身。

- 原全场景测试现在核对 27 个独立运行环境及清理、27 组业务断言、30 次 Judge 和 66/15/12 输入计数。
- 新增三组正向验证：两轮实际工具卡片进入 Judge 当前背景，同事项历史保留四条，预期断言不会进入 Judge 输入。
- 新增六组反向验证：每个医疗 case 分别移除卡片或对应角色；即使替身 Judge 两轮都给满分，业务结果也必须失败。没有用语言评分替代业务断言。

实际命令：

```text
.venv/Scripts/python.exe -m pytest tests/test_evaluation.py -q --junitxml=.scratch/U001-evaluation-inputs.xml
```

结果：**42 passed，78.06s**。JUnit 位于忽略目录 `.scratch/`；以上测试入口可重复运行。数据与测试文件的 `git diff --check` 通过。

编辑前 GitNexus 显式绑定当前工作树，对 `ScenarioChat.run` 的符号 ID 执行上游检查，返回 UNKNOWN / lower-bound：有 17 处 `run` 调用因接收者类型未解析而不在图中。没有把零条图边当作零影响；已源码核对 `RuntimeFactory` 装配、evaluator `_turn` 调用与本测试中包裹/替换 `run` 的反例。未重建索引；最终全树分析由 root 统一执行。

## 待实际验收

后续在完成 U001-12 后，用真实部署、相同输入与已记录模型参数运行，保存实际调用/Judge/业务失败和 candidate 报告。本次 42 项通过只能证明测试替身下的数据接线及评测断言行为；旧 54/12/12 候选报告不能推算新增场景质量，人工接受仍须独立决定。
