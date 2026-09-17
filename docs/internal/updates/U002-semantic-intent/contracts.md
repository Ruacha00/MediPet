# U002 跨模块契约

版本：准备稿 v1，2026-09-17。用于开工；模型 revision、包版本和阈值数值需在对应 issue 通过实测后固定，不能把待验证项写成当前事实。

## 1. 模型边界

- 首选 `BAAI/bge-small-zh-v1.5`，中文、512 维、512 token 上限（含特殊 token）、CLS pooling + L2 归一化；消息和模板均不加检索前缀。本次使用短句相似匹配，不复用 RAG 的查询指令。
- 默认实现路径为本地 FP32 ONNX。官方仓库当前未提供 ONNX 成品，因此从固定官方 revision 在隔离构建环境导出，并与参考实现对齐。不开启模型远程代码；不直接套用 MiniLM 的 mean pooling。
- `config/intent_embedding_model.json` 保存模型 ID、完整 revision、权重/tokenizer/配置/导出文件 SHA-256、导出工具版本、pooling、normalization、max_tokens、prefix 和 license。文件名和这些值共同确定 `space_id`，不能仅靠维度判断兼容。
- 模型文件放入被忽略的 `data/models/intent/` 或构建缓存，Git 只保存清单和脚本。构建依赖独立于生产依赖，具体版本由 02 在 Python 3.12 与 Chroma 0.5.23 约束下锁定。
- 向量适配器独立于 Anthropic/DeepSeek 聊天客户端。首版不实现远程 embedding API；未来可扩展批量接口，但不为未请求的供应商预建框架。

## 2. 小型适配接口与生命周期

在 `core/intent_embeddings.py` 定义小型批量编码接口，提供 `initialize()`、`encode_batch(texts)` 和只读 `status()`。实现语义和原字符哈希两个后端，可注入假后端；编码结果携带向量、实际 `space_id` 和耗时。调用者要求后端返回统一空间，不允许编码器对批内个别文本静默改用哈希。

实施冻结接口：`IntentEmbeddingProvider(config=None, *, semantic_backend=None)`；`EmbeddingConfig.from_env()`；异步 `initialize()` / `encode_batch(texts)` / `aclose()`，同步 `status()`。`EmbeddingBatch`字段为vectors/space_id/generation/backend/model/dimension/elapsed_ms/truncated（逐条布尔值）。elapsed_ms是本次批次含排队的总耗时，纯推理耗时由性能评测独立统计。显式恢复创建新provider，不开放业务请求中的重试/重载入口。

识别器接受 `embedding_provider` 与可注入 `calibration`；后者格式为 `schema_version`、`template_fingerprint`、`pattern`（rule_fingerprint/min_score）、`backends`（以semantic/hash为键，各含space_id/min_score/min_margin）。缺省从 `config/intent_embedding_calibration.json` 读取；无配置时正式向量弃权，实验可读取 `_embedding_recognize()` 的原始 `ranking`。新增运行数据仅使用JSON文件，不引入数据库。

校验返回条数、固定维度、有限数值、非零范数；异常由调用者统一处理。余弦计算遇到不同空间或维度直接拒绝，禁止 `zip` 截短后继续出分。空文本、非法 Unicode、超 token 限制均有确定行为；超长文本按 tokenizer 的固定规则截断并标记 `truncated`，不把字符数等同 token 数。

初始化和推理移出事件循环；模板批量编码，默认单个推理任务在途，ONNX 线程数可配置。队列等待和推理超时均需有界。Python 线程超时并不保证底层推理立即停止：超时后的工作不能更新模板/结果缓存，未完成前不无限提交同类任务，生命周期退出负责回收资源。

应用进程共享一个只负责编码的模型实例，不共享用户消息、IntentResult 缓存或可变学习模板。API 意图评测识别器、主编排器、隔离业务评测的识别器统一注入该后端；直接实例化保留可用的配置入口。初始化失败按 fallback 配置进入可观测的哈希或关闭向量状态，不阻塞固定急症处理。

## 3. 配置与故障状态

| 计划配置 | 含义 |
| --- | --- |
| `MEDIPET_INTENT_EMBEDDING_BACKEND` | `semantic`（目标默认）、`hash`（显式离线/对照）、`disabled`（关闭向量） |
| `MEDIPET_INTENT_EMBEDDING_MODEL_DIR` | 已准备的本地模型目录；请求处理中不联网补文件 |
| `MEDIPET_INTENT_EMBEDDING_FALLBACK` | `hash`（默认）或 `disabled` |
| `MEDIPET_INTENT_EMBEDDING_TIMEOUT_MS` | 含排队的向量查询等待上限，初始候选 2000 ms，05/08 实测后固定 |
| `MEDIPET_INTENT_EMBEDDING_THREADS` | CPU 推理线程数，初始候选 2，05 实测后固定 |

模型类型、pooling 和 revision 由清单控制；不提供能与实际权重不一致的自由维度配置。错误配置值应明确报告，运行时模型缺失/损坏/推理失败才按 fallback 处理；聊天模型密钥继续使用已有配置。

状态转移：初始化成功 → `semantic_ready`；语义初始化/推理失败 → `hash_fallback` 或 `disabled_fallback`；显式 hash → `hash_explicit`；显式关闭 → `disabled_explicit`。两种 disabled 状态均走既有 `.85/.15` 两路权重；hash 状态仍是三路权重，但须通过自己的接受门槛。首版故障降级在本进程内保持，重启或明确的初始化流程恢复，不在每个请求上反复探测。降级会更新后端 generation，使所有识别器下一次使用对应空间的模板；在途旧 generation 结果不能重新发布。

一次向量分类只允许“语义查询 + 全套语义模板”或“哈希查询 + 全套哈希模板”。模型不可用时整路切换并重算，不使用半套语义模板；哈希也不可用则本路弃权。

## 4. 模板、learn 与结果缓存

- 每个识别器从同一初始模板快照复制实例模板，`learn()` 只更新该实例；继续是进程内追加样本，不是训练权重，也不承诺持久化或跨实例传播。
- 模板缓存键包含 `space_id + template_revision`。锁内复查、取快照、批量构建、完整校验后原子发布；并发首次请求共享构建结果。在途发生 learn 或后端切换时丢弃旧版本，不按变动后的长度切片。
- 结果缓存键加入模型空间/generation、模板和校准版本，保留日期及模型实际消费的历史范围。消息应按实际分类输入生成指纹，去掉当前“仅前 200 字”的键冲突；当前 LLM 消费最近三条的完整内容，因此对完整清洗后的三条历史生成指纹，不沿用旧缓存的 160 字截断。
- learn、显式后端变化和阈值版本变化使旧结果失效；LLM 失败或运行时降级得到的结果不进入常规长存缓存。缓存命中标记不能冒充本次向量推理耗时。

## 5. 融合与拒绝判定

保留三路并行与类别加权投票。正常三路权重固定为 `0.7/0.2/0.1`，明确关闭向量时 `0.85/0.15`；正常最终阈值仍为 `0.5`。保留宽泛意图到细粒度关键词修正与急症前置边界，不在本轮调整主辅 Agent 路由算法。

每类取模板最大相似度，再取最高和次高的**不同意图类别**；不能用同类别两个模板计算分类分差。语义与哈希分别记录 top1、runner-up 和 margin。余弦不是概率；只有通过本后端的最小分数与分差门槛，向量路才贡献分数，否则以零分弃权。

门槛通过开发集确定，写入 `config/intent_embedding_calibration.json`，绑定模型空间/预处理指纹，并保存校准时的模板指纹用于复现。缺少校准配置或模型空间/预处理不匹配时标为 `uncalibrated`，正式服务中向量一律零分弃权；实验执行器可显式读取原始排名进行校准，不把实验模式用作默认服务配置。关键词强匹配门槛独立绑定关键词规则版本，不因向量模板变化失效。

为保留已有追加样本能力，实例内 `learn()` 增加模板时保持已冻结的同模型门槛参与正常三路融合，同时标记 `template_changed_since_calibration=true`，使旧分类缓存失效，并取消该向量在 LLM 故障时独立定类的资格；完成新模板开发集校准后方可恢复。模型、pooling 或预处理变更不适用这个例外。报告区分原校准模板与追加后的模板，不把原留出成绩继承给新增模板；本轮不建设在线自动校准或持久化训练系统。

LLM 失败时取消“向量分数 > 0 就采用”的捷径：

1. 清晰、无冲突的细粒度关键词结果优先；强关键词的接受门槛也写入校准记录。
2. 语义向量只有达到已冻结的分数与跨类别分差门槛、且没有与强关键词冲突时才可单独返回。
3. 哈希降级仅在达到自己的较保守门槛且有一致关键词支持时返回，不凭一个正数独立定类。
4. 强信号冲突、上下文不足或双方都不可靠时返回 `OTHER`，进入现有澄清路径；不是生成虚假的诊断或执行预约。

上述关键词优先与冲突拒绝在实现中使用明确测试冻结，不扩展成新的分类框架。否定语义与意图类别不是一一对应关系；急症固定规则和预约确认仍决定实际行为，不依赖 embedding 理解所有否定表达。

## 6. 可观测与兼容

`IntentResult.source_scores` 保持浮点字典；新增独立可选 `embedding_info`，API 映射为可选 `intent_embedding`，不改变原字段类型。至少记录 configured/active backend、model、space_id、dimension、template_revision、calibration_version、status、fallback_reason、top1/margin、accepted、truncated、cache_hit 及本次编码耗时。未实际推理时耗时用空值，不虚构零延迟。

`/health` 增加可选后端状态摘要，不返回密钥、完整路径或用户文本。已有运行详情无需新增强制界面。评测 metadata 从实际识别器读取后端及降级统计，移除当前硬编码 `local character n-gram`；发生降级的样本单列，不能算作真实语义成功。

此契约的持久报告只使用合成场景。现有 RAG、记忆模型与 Chroma 集合保持独立，不因意图模型维度变化重建向量库。
