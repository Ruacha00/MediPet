# U002-07 执行器验证与08交接

入口：[执行器](../../../../../../evaluation/semantic_intent.py)、[确定性测试](../../../../../../tests/test_semantic_intent_evaluation.py)、[影响分析](impact.json)。本任务不运行正式dev/holdout推理、不调用真实DeepSeek、不访问业务存储。测试数据仅临时创建的4条合成输入，不读取正式留出样本。

三类输出均为schema_version=1，候选文件禁止覆盖已有路径：

- `intent_rankings`：原始类别ranking、实际后端/空间、模板和源码指纹、逐例错误，主/低重合/OTHER/上下文分组及实际后端分组；加载、模板、首查询、至少100次暖查询和进程峰值RSS独立计时。`quality_valid=false`表示发生降级、分类失败或实际后端不符，不能作为真实语义质量通过证据。
- `intent_llm_responses`：实际生产prompt和解析器；只给模型message/history，不发送gold/家族/标注理由。保留请求/原响应/解析结果及指纹。默认单并发，最多3；SDK `max_retries=0`，显式60秒超时。固定急症不发模型请求。
- `intent_fusion_replay` / `intent_threshold_grid`：调用生产`recognize`、`_apply_embedding_calibration`及`_vote`，同一份冻结LLM响应用于三模式；只在dev允许每次最多25组合的网格，网格内不做向量或LLM推理。接受准确率和覆盖率给明确分母，全部拒绝的准确率为空，OTHER不会从错误/混淆表消失。`fusion_quality_valid`还要求live_sdk来源且无LLM失败；它表示证据完整，不表示已满足效果门槛。

JSONL先fsync `started`（case/input/request指纹及请求），再调用SDK，最后fsync `completed`。只补真正缺失ID；存在started但没有completed时明确报uncertain并阻断自动重发。已完成的超时/无效响应仍为完成记录，续跑不重试。NaN解析和无法序列化的响应保存可读原文及失败，不因落盘异常变回缺失样本。指纹不一致、重复行或破损行拒绝续跑；报告不自动接受基线。

## 08使用入口

以下命令为交接方法，07没有执行这些正式数据命令。使用当前根目录Python环境；模型与密钥由08通过已授权环境注入，不在命令或证据中填密钥。

```text
python -m evaluation.semantic_intent rank --dataset evaluation/cases/semantic_intent/dev.json --manifest evaluation/cases/semantic_intent/manifest.json --backend semantic --output <new-semantic-rank.json>
python -m evaluation.semantic_intent rank --dataset evaluation/cases/semantic_intent/dev.json --manifest evaluation/cases/semantic_intent/manifest.json --backend hash --output <new-hash-rank.json>
python -m evaluation.semantic_intent collect-llm --dataset <dataset.json> --manifest evaluation/cases/semantic_intent/manifest.json --checkpoint <raw.jsonl> --output <new-raw-summary.json> --concurrency 3 --timeout 60
python -m evaluation.semantic_intent replay --dataset <dataset.json> --manifest evaluation/cases/semantic_intent/manifest.json --backend semantic --rankings <semantic-rank.json> --llm <raw-summary.json> --calibration config/intent_embedding_calibration.json --output <new-replay.json>
```

hash重放替换backend/rankings；disabled省略rankings。正式holdout每个动作均须显式`--allow-holdout`，且manifest审阅为approved、文件hash吻合；网格始终拒绝holdout。原66条回归也可作为dataset，manifest检查其original_regression_sha256。文件输出路径由08选在独立证据目录，不能覆盖已有报告。源文件、模板、数据变化后原checkpoint不会继续收费采集。

开发集故障门槛可用下列程序入口，不需要真实LLM；报告明确`provenance=injected_failure`、`model_requests=0`，只看`llm_failure`，不能当融合质量：

```python
from evaluation.semantic_intent import load_dataset, read_json, build_failure_fixture, replay_grid

dataset = load_dataset("evaluation/cases/semantic_intent/dev.json",
                       manifest_path="evaluation/cases/semantic_intent/manifest.json")
rankings = read_json("<backend-rank.json>")
raw_failure = build_failure_fixture(dataset, rankings)
# calibration按contracts绑定排名中的space_id、template/pattern指纹。
# combinations读取08已预先冻结的25组，格式[{"min_score": ..., "min_margin": ...}, ...]。
report = await replay_grid(dataset, rankings=rankings, llm=raw_failure,
                           calibration=calibration, backend="semantic", combinations=combinations)
```

这是已有事件循环中的调用片段，`calibration/combinations`由08预冻结输入提供。正式模型阶段在冻结门槛后采集一份响应，再用同一raw重放三模式，不用正确答案补失败。

## 已执行验证

命令：`.venv/Scripts/python.exe -m pytest tests/test_semantic_intent_evaluation.py -q --junitxml=docs/internal/updates/U002-semantic-intent/evidence/evaluation/tests.xml`。

首轮13 passed/1 failed为Windows测试文件默认GBK读取UTF-8的测试错误，已改显式UTF-8；中间版15 passed。交叉只读审阅随后要求补写前日志和语义质量有效标志，最终[JUnit](tests.xml)为19 passed，52.98秒；最后补并发中断清理的[定向检查](interruption-tests.xml)为1 passed/18 deselected，3.30秒。测试包括独立手算指标、边界分母、正式holdout访问保护的临时同名数据、只补缺失ID、started不确定结果、NaN/序列化失败持久化、三模式复用、生产投票调用、无推理网格、降级单列/质量无效及禁止覆盖报告。它们不证明真实embedding或DeepSeek分类效果；实际质量与性能由08/09验收。
