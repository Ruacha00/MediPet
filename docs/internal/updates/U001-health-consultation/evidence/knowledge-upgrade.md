# U001 内置知识升级验证

初始化原本只添加缺失ID，已存在的医疗边界文字会在升级后继续保留。root先对 `_load_default_docs` 做当前树上游分析（LOW，直接调用 KnowledgeBase.__init__），源码确认只有初始化消费；保留公共 add_documents 的上传去重行为。

新 `_sync_default_documents` 按本次内置文档ID读取已有片段，只有内容或元数据变化才upsert，随后删除该文档已消失的旧片段。未出现在本次来源中的其他文档和集合保持原状，不清空知识库、预约或历史。

`tests/test_knowledge_skills.py` 34项通过：新例用多片旧内容升级为单片新规则，确认旧片删除、用户上传保留、再次初始化不重复嵌入。

真实 Chroma 0.5.23 验证在 medipet-validation 完成，见 [storage.json](storage.json)。实际内置24篇/24片已存在且二次初始化不变；另建本次UUID合成集合，从4个旧片升级到1个新片并保留1个用户上传。仅删除该UUID测试集合，现有业务集合和数据卷不变。这证明升级行为与存储接线，不证明语义召回质量。
