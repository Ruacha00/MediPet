# U002-02 编码器与真实模型验收

日期：2026-09-17。仅涉及本地编码器；没有调用聊天模型、修改识别器或运行意图质量评测。

## 来源与运行边界

- 官方模型：[BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5/tree/7999e1d3359715c523056ef9478215996d62a620)，固定 revision `7999e1d3359715c523056ef9478215996d62a620`，模型卡声明 MIT。
- 按该固定 revision 的 `/resolve/<revision>/<filename>` 下载官方资产。源权重/tokenizer/config/pooling/模型卡 SHA-256 固定在导出脚本与[模型清单](../../../../../../config/intent_embedding_model.json)；导出脚本遇到源摘要不符即退出，不使用浮动 main。
- 本地 FP32 ONNX 输出 512 维，CLS pooling + L2；最大 512 token 含特殊 token，右侧截断，双方无检索前缀。处理方式依据[官方使用说明](https://huggingface.co/BAAI/bge-small-zh-v1.5#using-huggingface-transformers)，语义相似度不当作概率。
- 生产运行只使用锁定的 ONNX Runtime 1.30.0、NumPy 2.5.3、tokenizers 0.20.3。构建位于 ignored `.scratch/embedding-build`，PyTorch 2.5.1+cpu、Transformers 4.46.3、ONNX 1.17.0 等独立锁在 `requirements-embedding-build.txt`。生产 `.venv` 实测没有 torch/transformers。
- 源资产在 ignored `data/models/intent/source-7999e1d3359715c523056ef9478215996d62a620`；运行资产为 ignored `data/models/intent/bge-small-zh-v1.5/model.onnx`、`tokenizer.json`，附 `MODEL_CARD.md`。运行时零下载，仅导出准备阶段访问官方公开来源。

## 接口与失效行为

`EmbeddingConfig(backend='semantic', model_dir=..., fallback='hash', timeout_ms=2000, threads=2, manifest_path=...)`；`from_env()` 仅读取 `MEDIPET_INTENT_EMBEDDING_*`。

`IntentEmbeddingProvider` 提供 async `initialize()`、async `encode_batch(texts)`、`status()` 和资源所有者调用的 async `aclose()`。返回 `EmbeddingBatch(vectors, space_id, generation, backend, model, dimension, elapsed_ms, truncated)`。`elapsed_ms` 是本次编码请求总耗时，含需要的初始化、等待及编码；不是纯模型推理耗时。

- 共享对象只持有模型、单工作线程和状态，不持有消息缓存、模板或校准。测试替身同步实现 `initialize/encode_batch`，提供 `model/dimension/space_id`；同样经过工作线程与结果校验。
- 初始化/编码失败将整个 provider 固定降级，generation 增加；显式恢复通过新 provider，不逐请求重试语义模型。哈希保持原 256 维字符 1/2/3-gram 集合、MD5 signed 桶输出。
- 一个 worker 在途，等待与计算共享请求时间预算。超时/取消不取消底层线程，不释放该线程槽；旧结果不发布。若剩余时间不足或旧任务仍占槽，当前调用抛 `EmbeddingUnavailable`，识别器应弃权；释放后后续调用可使用已确定的哈希后端，不额外排积压任务。
- 不允许批内部分文本换后端；模型完整批次数量、维度、有限值、非零向量和截断标记均验证。disabled 状态明确抛 `EmbeddingUnavailable`，错误配置直接 `ValueError`。
- 空批次返回空向量；单个空白文本或清洗后为空明确 `ValueError`，不把非法输入认成模型故障。合法文本中的 Unicode 代理字符按现有清洗规则移除。
- 模板与输入空间比对、校准和结果缓存属于 03/04 的识别器；编码器不跨空间做相似度。

## 真实证据与复现

在 Python 3.12.7 / Windows 11 中执行：

```powershell
uv venv .scratch/embedding-build --python .venv/Scripts/python.exe
uv pip install --python .scratch/embedding-build/Scripts/python.exe -r requirements-embedding-build.txt --index-strategy unsafe-best-match
# 官方固定资产准备好后，导出步骤只读本地源文件：
& .scratch/embedding-build/Scripts/python.exe -X utf8 scripts/export_intent_embedding.py --source-dir data/models/intent/source-7999e1d3359715c523056ef9478215996d62a620 --evidence docs/internal/updates/U002-semantic-intent/evidence/embedding/reference-parity.json
& .venv/Scripts/python.exe -X utf8 -m pytest tests/test_intent_embeddings.py -q
$env:MEDIPET_TEST_INTENT_MODEL='1'
& .venv/Scripts/python.exe -X utf8 -m pytest tests/test_intent_embeddings.py -q
Remove-Item Env:MEDIPET_TEST_INTENT_MODEL
```

- [参考对齐明细](reference-parity.json)：22 条独立合成中文/中英混合/长短输入，最低 cosine `0.9999999701211565`，全部 ≥0.999；批量与逐条最大绝对误差 `0.0`，含超过 512 token 的截断样本。参考实现是固定 Transformers 的 CPU FP32 eager AutoModel，单条编码取 CLS 并由 torch 做 L2。
- 普通离线测试：**31 passed、1 skipped**（0.49s）；显式开启真实本地模型后：**32 passed**（1.01s）。这两次是同一套，不叠加数量。
- 并发首次调用仅初始化一次；事件协调证明模型阻塞时事件循环可继续、取消不释放工作槽、超时不产生第二个语义任务、后续只返回新 generation 哈希结果。
- 初次测试捕获工作线程闭包引用了后一次循环的 fallback 维度，已改为提交时绑定状态快照；修复后上述测试通过。缺失/损坏模型、批量错误与哈希原算法固定摘要均覆盖。
- 安装 session `42480`、真实导出 session `81662` 均退出 0，无遗留后台任务。`git check-ignore` 确认模型与隔离环境被忽略。

本记录不证明真实意图分类提升、Linux 容器兼容或部署延迟达标；这些由 05/07/08 后续验收。共享编码器接线与固定急症绕过模型也由后续集成验证。
