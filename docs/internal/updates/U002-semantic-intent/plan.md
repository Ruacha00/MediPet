# U002 实施计划

依据：[更新意图](intent.md)与[跨模块契约](contracts.md)。所有代码、配置路径均为预计改动，当前状态只维护在 issues 中。

## 实施拆分

| Spec | 交付 | 原子任务 |
| --- | --- | --- |
| [S01 模型与编码](specs/S01-embedding.md) | 来源固定、中文编码器、哈希后备、批量接口 | U002-01、02 |
| [S02 融合与接线](specs/S02-fusion.md) | 三入口一致、模板缓存、故障投票与诊断 | U002-03、04 |
| [S03 评测与校准](specs/S03-evaluation.md) | 独立数据、可复现对照、冻结阈值 | U002-06、07、08 |
| [S04 运行与交付](specs/S04-delivery.md) | 离线模型打包、真实链路、原文档增量更新 | U002-05、09、10 |

## 直接依赖与负责人

| Issue | 可独立验收的结果 | 直接依赖 | 实施负责人 |
| --- | --- | --- | --- |
| [U002-01](issues/U002-01.md) | 范围、契约与影响分析可用于实施 | 无 | root |
| [U002-02](issues/U002-02.md) | 固定中文模型与本地批量编码器 | 01 | spec_agents_knowledge |
| [U002-03](issues/U002-03.md) | 三入口接线、同空间模板与缓存一致性 | 01 | root |
| [U002-04](issues/U002-04.md) | 明确降级投票及失败矩阵 | 03 | root |
| [U002-05](issues/U002-05.md) | 模型准备、容器离线加载与配置 | 02 | spec_api_ui |
| [U002-06](issues/U002-06.md) | 独立开发集、留出集和标注契约 | 01 | audit_preparation_scope |
| [U002-07](issues/U002-07.md) | 三模式对照执行器和可审计报告 | 03、06 | audit_preparation_scope |
| [U002-08](issues/U002-08.md) | 开发集校准及一次冻结留出集对照 | 04、05、07 | root |
| [U002-09](issues/U002-09.md) | 真实模型、隔离业务及故障恢复验收 | 08 | spec_api_ui |
| [U002-10](issues/U002-10.md) | 教程增量修订、证据汇总与最终检查 | 09 | root |

共 10 个节点、12 条直接依赖。issue 头部 `depends_on` 是调度依据；任务状态为 `todo/in_progress/blocked/done`，准备阶段只有 01 可关闭。最终门槛未满足时不将下游记为完成。

## 并行批次与文件所有权

1. 01 完成后：02 模型适配、03 按冻结接口接线、06 数据准备并行。实施开始时已冻结 provider/batch/config 接口，03 不必等待模型下载和导出，但不以假后端证明真实向量效果。
2. 02 完成后：05 容器准备可开始，03/06 可继续；08仍经05等待02的真实模型数值验收。
3. 03、06 完成后：04 故障投票与 07 对照执行器并行；05 可继续。
4. 04、05、07 完成后串行执行 08 校准、09 真实验收、10 文档交付。

| 文件范围 | 唯一写入安排 |
| --- | --- |
| `core/intent_embeddings.py`、`config/intent_embedding_model.json`、`scripts/export_intent_embedding.py`、`requirements-embedding-build.txt`、`requirements.txt`、`tests/test_intent_embeddings.py` | 02；08 如发现适配问题，交回该负责人修复 |
| `core/intent_recognizer.py`、`tests/test_intent_recognizer.py` | 03 → 04；08 仅更新独立校准配置，改算法须重开相应 issue |
| `agents/agent_orchestrator.py`、`api/main.py`、`api/evaluation_runtime.py`、`evaluation/evaluator.py` 与其现有接线测试 | 03；09 只增加独立集成测试，不并发编辑这些文件 |
| `.env.example`、`Dockerfile`、`docker-compose.yml`、`.dockerignore`、`scripts/prepare_intent_embedding.py`、`tests/test_intent_embedding_runtime.py` | 05 |
| `evaluation/cases/semantic_intent/`、`tests/test_semantic_intent_dataset.py` | 06，冻结后不根据留出集结果改标签 |
| `evaluation/semantic_intent.py`、`tests/test_semantic_intent_evaluation.py` | 07 |
| `config/intent_embedding_calibration.json` | 08；03/04 读取契约和保守的未校准状态，不提前写入最终阈值 |
| `tests/integration/test_semantic_intent_runtime.py`、本更新真实验收证据 | 09 |
| 更新计划、状态与汇总；既有教程/简历资料 | root；10 执行教程增量修订 |

以上文件名是预定落点，不代表文件已存在。改变文件归属先在检查点交接；05 不抢写 02 的依赖清单，运行依赖差异交回 02。实现需要扩大范围时更新 `touches` 并检查文件冲突。

## 执行控制

- 总并发 root + 最多 3 子 agent；复用既有 agent，不递归派生。每次只派发直接前置完成、输入明确的任务，任务交回后停止该工作线。
- 先做编码器与确定性测试，再做真实 embedding，最后才调用 DeepSeek。单元测试不得隐式下载模型或访问真实聊天接口。
- 开发集阈值搜索每种后端最多 25 组，网格先固定；正常融合权重本轮不搜索。留出集一次正式冻结对照；因缺陷修复重跑时保留旧结果，说明测试集已被查看。
- 同类失败连续两次转诊断，不无限换版本、换模型或循环请求。ONNX 路线未通过时记录具体障碍，修订方案后再决定是否采用隔离的 Sentence Transformers 运行方案；不静默换实现。
- 模型准备和构建允许访问固定官方来源；请求处理阶段不下载、导出模型或反复重试。测试进程、端口、临时目录和证据位置进入检查点。
- GitNexus 刷新由 root 串行执行；每项代码修改前针对具体符号补影响分析。提交前对本工作树做完整变更分析，partial/truncated 不作为通过。
- 上下文压缩/交接前更新 EXECUTION：当前 issue、文件负责人、已完成证据、剩余问题、后台进程和下一步。不得凭上下文缺失重复跑付费模型或清理其他工作。

## 完成条件

四份 spec 的硬性验收满足，真实语义后端确实启用且留出对照达标；固定急症、预约确认与身份边界无回归；故障时有明确哈希或关闭向量状态；模型文件可离线重用；学习资料准确对应代码与证据。未达到效果门槛时保留候选并记录原因，不以测试替身或配置字段存在宣称语义接入成功。
