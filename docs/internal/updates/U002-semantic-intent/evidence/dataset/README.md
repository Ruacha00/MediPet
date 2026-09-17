# U002-06 数据准备证据

2026-09-17，负责人 `/root/audit_preparation_scope`。只新增独立合成数据、数据检查和本任务记录；没有修改识别器、模板、原66条意图或原多轮/边界集，没有运行任何模型。

- 数据入口：[开发集](../../../../../../evaluation/cases/semantic_intent/dev.json)、[留出集](../../../../../../evaluation/cases/semantic_intent/holdout.json)、[标注与规则](../../../../../../evaluation/cases/semantic_intent/README.md)、[指纹清单](../../../../../../evaluation/cases/semantic_intent/manifest.json)。
- 18个原覆盖类别分别dev6/holdout10条基础样本，共288条；另增12条上下文/歧义控制。开发集114条、留出集186条；主指标96/160条，具体分组和低重合分布见[counts.json](counts.json)。
- 留出低字面重合132条、14个普通业务类别；算法与阈值在推理前固定。没有使用向量结果或LLM评分挑样本。数据低字面重合不是模型效果，也不是语义完全独立的证明。
- 基础144个成对表达家族按完整家族分割；字符近似扫描仅标记一条“仅否认取消”的开发集歧义输入与基准模板相近，保留为控制例，不进入任何主指标或可判定故障覆盖率。独立审阅者已确认该边界处理。
- [影响核对](impact.json)：显式当前工作树，基准索引7a4e22a；现有 `_load_cases` 只读取三个命名文件，本任务未改它。新增测试尚未进入索引，最终变更分析由root完成。

验证命令：`.venv/Scripts/python.exe -m pytest tests/test_semantic_intent_dataset.py -q --junitxml=docs/internal/updates/U002-semantic-intent/evidence/dataset/tests.xml`。

结果：[JUnit](tests.xml)记录8 passed，2.54秒；检查不导入业务模型、不联网、不访问Redis/Chroma。它证明数据结构与检查契约，不证明语义分类正确率。独立审阅者 `/root/spec_agents_knowledge` 已审阅288条基础及12条控制，发现4组跨split框架相近；随后8条holdout实质替换已复核通过，详见[逐项记录](review-changes.json)。OTHER/否定控制保留限制通过，最终指纹已冻结。整个数据编写与两次人工复核均未调用模型；这是有界标注审阅，不是语义完全独立的统计证明。
