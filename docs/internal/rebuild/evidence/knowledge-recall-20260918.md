# 中文知识召回修复：专项验证

2026-09-18；工作树 `D:/Projects/Agent Learn/Project/MediPet-Rebuild`，起点 main `bf4f3538b5b2277497d6bed75f68569d97a9229e`。仅调整知识候选召回；重排正文投影由主 agent 独立处理。

## 诊断与修改前影响

- 已读 AGENTS、IMPLEMENTATION_PLAN、重构索引/执行检查点及 diagnosing-bugs、gitnexus-impact-analysis。
- GitNexus 绑定上述绝对路径，索引同起点：4803 nodes、11516 edges、339 flows。`KnowledgeBase.search` / `add_documents` 的上游结果为 UNKNOWN（零静态调用边），未把它当作无影响。
- 源码补核：`search_async` 经 `asyncio.to_thread` 调用 `search`；`search_handler` 注册到工具管理器；API `/search`、Agent 共享 RAG 经过 `search_with_rewrite` 的并行召回。上传 API 调用 `add_documents_async`。查询流程图包含 `Search_with_rewrite → Handler`，动态 handler 与线程函数引用需要源码补核。
- 24 篇知识文件的 SHA-256 与旧40题报告完全一致。全量文档均在独立临时 Chroma 中存在，目标内容未被切掉，排除旧集合和切片漏内容。
- 临时 Chroma 使用本机已缓存的 all-MiniLM-L6-v2，直接40题 Recall@3 重现为 26.25%。取消预约文档排22、无障碍排9、布洛芬排11；27/40题至少一个必需文档落在前5之外。单独扩大重排候选不能修复召回链的全部缺口。
- 新增测试先回放这三题实测向量前三，在实际 `KnowledgeBase.search` 入口断言目标片段；修改前得到 **3 failed**，修改后均通过。

## 最小实现

- 新增 `mcp/knowledge_retrieval.py`：Unicode宽度归一化、英文小写词/数字词和中文相邻双字；BM25从标题与正文计算词法排名，标题计两次。不含查询到文档ID的映射或评测专用词典。
- `KnowledgeBase.search` 保留 Chroma默认 embedding、存量集合、距离返回和异常传播。每路候选最多 `min(max(top_k*2,5),count)`；以片段ID去重，加权RRF融合后只返回 `top_k`。
- 固定参数：BM25 `k1=1.2,b=0.75`；RRF平滑常数10，向量权重1、词法权重2。`score_type=hybrid_rrf` 时 `score` 是排名分，`vector_score` 与 `lexical_score` 保留分路证据，不称相似概率。没有词法证据时仍返回原向量顺序和分数；向量空结果或错误保持原行为。
- 当前24片演示库按请求读取 Chroma文本快照计算词法；没有新增缓存或数据库，因此上传及同片数内置内容更新立即可见。整库只在服务端计算，不传给外层重排模型。
- 知识库/工具管理器接口与来源字段不变。无依赖、配置、embedding迁移或在线集合修改。

## 验证结果与限制

1. 离线专项及既有知识/Skills测试：`python -m pytest tests/test_knowledge_recall.py tests/test_knowledge_skills.py -q`，44 passed、1 skipped。覆盖实测漏召回、无预定义ID的中文/英文上传、同片数更新、来源、语义兜底、异常和并行上限。
2. 显式设置 `MEDIPET_TEST_LOCAL_RETRIEVAL=1` 后专项测试 **11 passed**：其中1项使用真实 Ephemeral Chroma 和缓存 embedding，另外10项为离线测试。只操作自己创建的唯一临时集合并在 finally 删除；无外部LLM调用。
3. 最终冻结代码的独立40旧题直接检索：Recall@3 **26.25% → 83.75%**，平均单查询0.03345秒（含本地embedding/查询/词法，非生产延迟）。原始逐题排名、失败、参数与源码指纹见[JSON](knowledge-recall-20260918.json)。这不是改写/重排完整链或回答质量指标。
4. 保留一次原命中回归 `retrieval-01`，最终8题未完全召回：01、10、14、28、33、34、38、39。词法无法保证处理同义表达或复合问题；后续冻结新旧双组评估负责检验完整链，不根据新题集调整本次参数。
5. 开发中曾试验“至少三个双字匹配”门槛；它把“眼科医生/儿科医生”等有效短证据排除，因此移除。该试验40题87.5%且有2条原命中回归；最终选择没有这个门槛的83.75%，没有择取较高试验值作交付指标。
6. 目前每次扫描全部文本的方案适用于小型演示知识库。大库尚未做吞吐验证；本次没有引入额外索引生命周期和缓存同步机制。

产品文件已交还主 agent 冻结；后续完整模型A/B及提交前GitNexus变更分析由主 agent 汇总。线上数据、部署、既有评测脚本和公共文档未由本任务修改。
