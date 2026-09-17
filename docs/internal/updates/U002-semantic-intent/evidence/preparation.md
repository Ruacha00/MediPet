# U002 准备依据

核对时间：2026-09-17。本文件是源码与方案检查记录，不是模型质量或性能验收。

## 工作树与影响

- 仓库/工作树：`D:/Projects/Agent Learn/Project/MediPet-Rebuild`；分支 `codex/u001-health-consultation`；HEAD `7a4e22a23d33a0bfa910f19662f2a8e40e429d54`，较远程领先 1 个提交。
- GitNexus 当前索引同一 HEAD，3929 节点、9020 边、248 流程；工具使用绝对工作树路径，未引用原 MediPet 的旧索引。
- `_embed_text` 上游：6 个符号，直接调用者 `_embedding_recognize`、`_load_template_embeddings`；涉及编排与评测，工具评级 HIGH。
- `_vote` 上游：5 个符号，直接调用者 `recognize`；涉及编排与评测，工具评级 HIGH。两次 impact 返回完整，无 partial/truncated；见 [impact.json](impact.json)。
- 手工补核 API 生命周期、AgentOrchestrator 构造器、`api/evaluation_runtime.py`：三条链分别创建识别器。图中方法依赖不能替代这三个创建入口的源码检查。
- 当前 `_cosine` 会用 zip 截断，模板逐条编码/无锁，learn 修改全局模板却只清本实例缓存，结果缓存缺少模型版本，LLM 故障接受任何正向量分数。均已纳入本次契约，未在准备阶段修改源码。
- 初始未跟踪 `docs/internal/rebuild/evidence/document-source-manifest.json` 保持原状。当前已跟踪的“文档+简历”不再按 U001 早期记录误认为全部未跟踪。

## 选型依据

首选 BAAI/bge-small-zh-v1.5：中文、512 维、512 token、MIT；官方配置使用 CLS pooling，参考示例做 L2 归一化。短句/模板相似匹配双方无检索前缀。它的相似度不能直接继承哈希阈值，需要本项目开发集校准。[官方模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5)、[配置](https://huggingface.co/BAAI/bge-small-zh-v1.5/blob/main/config.json)、[pooling 配置](https://huggingface.co/BAAI/bge-small-zh-v1.5/blob/main/1_Pooling/config.json)。

官方文件树当前没有 ONNX 成品。因此选择隔离导出再对齐参考实现，而非假定官方已有可直接部署的 ONNX 包。[官方文件树](https://huggingface.co/BAAI/bge-small-zh-v1.5/tree/main)、[Sentence Transformers ONNX 说明](https://sbert.net/docs/sentence_transformer/usage/efficiency.html#onnx)。

本仓库尚无 Sentence Transformers/PyTorch 运行依赖；已有 Chroma 0.5.23 引入 ONNX Runtime、NumPy、tokenizers，后者版本约束需保持兼容。计划优先复用这组运行依赖，构建工具独立锁版本。[Chroma 0.5.23 依赖](https://github.com/chroma-core/chroma/blob/0.5.23/requirements.txt)。

待实施验证：固定完整模型 revision、导出工具/运行库版本、Windows/Linux CPU 实际兼容性、内存与延迟、独立数据上的提升。尚未下载模型、导出、安装依赖或执行真实向量推理。

## 并行准备

三位既有子 agent 分别完成只读的接入/缓存审查、官方模型及依赖核对、评测/标注审查；root 汇总契约与任务。没有子 agent 修改业务代码或运行外部模型。后续实施分工见计划，当前没有后台测试、模型下载或构建进程。

## 准备验收

10个节点、12条直接依赖无环，全部可达最终交付，无未排序的文件写入冲突；新增材料及三个更新入口的本地链接检查通过。独立复核的五个契约/评测歧义已处理。字符哈希/语义模型质量、依赖安装和真实运行均留待实施，不以本次文档检查替代。
