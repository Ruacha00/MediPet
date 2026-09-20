# MediPet 测试、评测与监控

程序测试检查接口、工具权限、身份隔离与状态变化；模型评测检查意图分类和回答效果。预约是否完成以库存、预约记录和回执为准，不能由模型回复评分替代。

## 本地测试

在已安装项目及开发依赖的Python环境中执行：

```powershell
python -m pytest -q
Push-Location frontend
node --test tests/*.test.mjs
npm run build
Pop-Location
```

最近完整后端运行（2026-09-18）为839通过、28跳过、1失败，原始结果见[测试XML](../evaluation/reports/delivery-recall-20260918/checks/delivery-recall-final-tests.xml)。唯一失败来自历史semantic_intent清单中intents.json与dialogs.json的冻结指纹不一致，不能通过改写预期哈希掩盖。跳过的真实组件检查需要显式环境配置。

- API及预约：[test_chat_api.py](../tests/test_chat_api.py)、[test_demo_workflow.py](../tests/test_demo_workflow.py)、[test_hospital_service.py](../tests/test_hospital_service.py)。前后端共用[业务数据契约](api-contracts.md)中的JSON样例。
- 复合任务与检索：[test_compound_delivery.py](../tests/test_compound_delivery.py)、[test_knowledge_recall.py](../tests/test_knowledge_recall.py)、[test_rerank_evidence.py](../tests/test_rerank_evidence.py)。
- 真实存储：[integration/test_storage.py](../tests/integration/test_storage.py)及[事务检查脚本](../tests/benchmarks/business_transactions.py)。应使用独立测试前缀与集合，按脚本约定显式启用。
- 浏览器：运行方法见[浏览器测试说明](../tests/browser/README.md)。

## 数据与执行路径

固定输入见[evaluation/cases](../evaluation/cases/README.md)：66条默认意图、15组多轮对话、12组边界；扩展任务另存为[business-expanded-20260918.json](../evaluation/cases/business-expanded-20260918.json)，包含80个业务任务。

[evaluator.py](../evaluation/evaluator.py)通过`case_runtime_factory`为场景建立独立状态，直接调用编排器和业务服务。`setup`是前置状态，`between_turns`和`after_turns`表示测试驱动的用户操作，包括切换患者和显式确认；它们不授予模型确认权限。这条路径不能替代HTTP接口或浏览器测试。

真实业务扩测示例：

```powershell
python -m evaluation.expanded_metrics --inputs evaluation/cases/business-expanded-20260918.json --output data/eval/business-check --execute
```

需要本地服务和有效模型配置，会产生模型调用。每次运行使用新输出目录，保留完整输入、参数、调用失败及源码指纹，不覆盖旧结果。

## 评分口径

- 意图：由固定标签计算Accuracy、Macro-F1及各类别指标。
- 业务状态：确定性断言检查身份、工具结果、实际卡片、库存、预约和回执。
- 端到端回复：LLM-as-Judge评价相关性、准确性、完整性和有用性，四维平均至少0.75视为质量通过；调用及Judge失败单列。
- 检索：按标注目标文档集合计算宏平均Recall@3，命中率与全部目标文档命中率另列。
- 检索证据回答：同一30题分别基于新旧实际召回片段生成回答，评价正确性、证据支持、需求覆盖、无依据断言和资料不足处理。五维均至少0.8、全部目标事实覆盖且无无依据断言才通过。评分失败留在固定分母，详见[专项方法](../evaluation/answer-quality.md)。

汇总结果项通过率不能当作意图准确率或预约完成率；只在模型、数据、参数与指标定义可比时解释前后变化。

## 最近实测

2026-09-18使用deepseek-flash、真实Redis/Chroma，完整输入及结果见[专项报告](../evaluation/reports/delivery-recall-20260918/notes.md)。

| 范围 | 结果 |
| --- | --- |
| 复合首轮号源与材料双交付 | 11/20→20/20 |
| 预约创建与取消 | 40/40，前后保持一致 |
| 上下文衔接 | 19/20→20/20 |
| 完整检索链Recall@3 | 40题，51.25%→97.5% |
| 检索证据回答综合通过 | 30题，16/30→28/30 |

回答专项新版保留1条Judge逐字引用无效与1条跨文档信息缺失。固定集结果不代表线上、任意输入或临床准确率。

中文语义分支已接入，但其冻结LLM故障场景接受准确率为69/84（82.143%），未达到90%的预声明门槛。该限制不因后续业务测试通过而关闭，原始对照见[对应版本报告](https://github.com/Ruacha00/MediPet/blob/60d7076/docs/internal/updates/U002-semantic-intent/evidence/calibration/implementation.md)。

## 候选与基线

普通运行写入`candidates/<run_id>.json`，记录模型、参数、输入指纹、响应、轨迹、卡片与失败，不覆盖已接受基线。只有显式调用`accept_baseline(report, reviewed_by=...)`才能保存人工复核后的accepted报告；比较器与已接受基线比较，相对下降超过5%时标记回归。

历史报告按原始字节保存在[evaluation/reports](../evaluation/reports/README.md)。其中旧路径、运行环境与实现说明属于报告当时的版本，不能视为当前维护说明。

## 运行监控

[PerformanceMonitor](../monitor/performance_monitor.py)采集Agent和工具成功率、延迟及状态，将监控惩罚反馈到同类实例的路由评分。API提供`/monitor`、`/trace/tools`、`/trace/tool/{request_id}`与`/metrics`。工具统计反映实际经过对应组件的请求，预约是否执行仍看业务回执；请求成功率不等于回答正确率。
