# U002-05 模型准备与离线运行验收

日期：2026-09-17。仅部署与编码器验收，未执行付费聊天、开发集校准或正式留出集；不代替 U002-08/09。Docker 恢复过程及用户重置归因见 [docker-recovery.md](docker-recovery.md)。

## 实现

- `scripts/prepare_intent_embedding.py` 提供三个显式子命令：`download` 从固定官方 revision 下载并校验 11 份源文件；`export` 校验本地源后用调用者指定的独立 Python 执行原离线导出器；`verify` 校验已导出模型/tokenizer/模型卡，`--smoke` 再真实编码一条合成消息。失败返回非零退出码，不以哈希启动冒充准备成功。
- 下载用临时文件，完整 SHA-256 通过后才原子发布；已有但损坏的缓存明确报错并保留，不静默覆盖。没有自动装包或运行期下载。导出只在显式命令发生，子进程设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`。
- Docker 构建上下文只允许 `data/` 中三份运行资产进入，不包含官方源权重、隔离构建环境或其他业务数据。镜像先验证资产，再放到 `/opt/medipet/models/intent/bge-small-zh-v1.5` 并移除写权限。`api-data:/app/data` 不会遮蔽它们。
- `.env.example` 用宿主机路径；Compose 明确覆盖为镜像内 `/opt` 路径，其他四项配置按契约传递。保持 2 个推理线程和 2000 ms 等待上限；本次实测支持此运行初值，分类门槛仍由 08 校准。
- GitNexus 显式绑定本工作树。Dockerfile 上游 LOW、5 个引用；Compose LOW、7 个引用，均无 partial/truncated。其图边主要是文档引用，因此另读 Compose build/volume 配置、`EmbeddingConfig.from_env` 和导出器源码，补核实际配置/镜像入口；未修改已交回的编码器/requirements。

## 可复现准备

从仓库根目录执行。首次下载和依赖安装需要网络；不读取真实 `.env`。

```powershell
# 下载固定官方源；已存在且校验正确时不重复下载。
.venv\Scripts\python.exe scripts/prepare_intent_embedding.py download

# 独立导出环境。已由 02 准备时复用，生产环境不安装此依赖文件。
python -m venv .scratch/embedding-build
.scratch\embedding-build\Scripts\python.exe -m pip install -r requirements-embedding-build.txt
.venv\Scripts\python.exe scripts/prepare_intent_embedding.py export --python .scratch/embedding-build/Scripts/python.exe --evidence .scratch/intent-export.json
.venv\Scripts\python.exe scripts/prepare_intent_embedding.py verify --smoke

# 完全已有资产时只读校验，不访问网络。
.venv\Scripts\python.exe scripts/prepare_intent_embedding.py download --offline
docker compose --env-file .env.example config --quiet
docker build --progress plain -t medipet-u002-runtime:check .
```

Linux 对应 Python 路径是 `.venv/bin/python` 与 `.scratch/embedding-build/bin/python`，子命令及参数相同。模型大文件在 Git 忽略的 `data/models/intent/`，提交只保留脚本/版本清单/证据。`export` 默认写版本清单；生产冻结后不要无理由重导出覆盖，本次包装命令验证使用 `.scratch/U002-05-export/` 独立输出，未改 02 冻结资产。

## 实际结果

固定模型为 `BAAI/bge-small-zh-v1.5`，revision `7999e1d3359715c523056ef9478215996d62a620`，space_id `semantic:d001ef9a85563852270fc1bfc59cb6e2ba3b6d9ed8b61a07be66f658f72efde3`。

| 检查 | 实际结果 |
| --- | --- |
| 默认运行测试 | 12 passed / 1 明确跳过真实模型 |
| 显式真实模型运行测试 | 13 passed，Windows Python 3.12.7；socket.connect 被禁止 |
| 本地源校验 | 11 份通过、0 份下载；复用 02 已下载缓存 |
| 通过新入口再次离线导出 | 22 条参考对齐，minimum cosine 0.9999999701211565；batch/single max error 0.0；输出模型 SHA 和空间与冻结资产相同 |
| 缺模型准备命令 | exit 1，FileNotFoundError；没有变成准备成功 |
| 独立生产镜像 | 构建 exit 0，`pip check` 无依赖冲突 |
| 隔离容器运行 | `--network none --read-only --cpus 2 --memory 2g`；uid 1000；模型不可写；`/app/data` 被空 tmpfs 遮蔽后仍 semantic_ready |
| Linux Python/依赖 | Python 3.12.14，ORT 1.30.0 / tokenizers 0.20.3 / NumPy 2.5.3 / Chroma 0.5.23 |
| 模型运行观测 | 冷初始化 334.81 ms；30 次查询（短句与截断长句），p50 3.76 ms、p95 79.09 ms、max 79.16 ms |
| 内存 | 进程峰值 RSS 323.18 MiB，包含本地意图与 Chroma MiniLM 兼容检查；不是整套服务或分类效果指标 |
| Chroma 兼容 | 原 MiniLM 缓存断网编码成功，仍为 384 维，未连接/修改原 Chroma 集合 |
| 缺失/损坏资产 | 分别为 `initialize:FileNotFoundError` / `initialize:ValueError`，状态 `hash_fallback`，后续真实哈希编码 256 维 |
| 生产镜像内容 | 无 torch、transformers、构建 requirements、`.scratch`、`.env` 或官方源缓存；镜像资产 SHA 已校验 |

本次离线运行 stderr 有一条 ONNX Runtime 无法在只读文件系统持久化 telemetry device ID 的警告，改用内存标识；测试和推理未失败，没有为消除警告放开网络或模型写权限。

另执行完整 `docker build --network none --pull=false`，**实际失败**：现有 `apt-get` 安装 Tesseract 时无法访问包源，构建步骤 exit 100，Docker 命令 exit 1。首次镜像构建仍需要基础镜像、Python 与系统包源；这里交付的是准备后可断网运行的镜像，不声称从空包缓存完全离线构建。未反复重试或新增部署基础镜像流程。

## 证据与重现

- [verification.json](verification.json)：镜像 ID、源文件 SHA、退出码与导出数值摘要。
- [container-offline.json](container-offline.json)：真实非 root/断网容器结果、延迟与故障状态。
- [offline_probe.py](offline_probe.py)：本次可重复的无服务/密钥/业务数据探针。
- [existing-docker-objects.json](existing-docker-objects.json)：结束时两套既有容器和六个命名卷的只读清单。八个容器仍停止，未启动旧 API；对象存在不等于内容已验证未变。
- 原始本地日志：`.scratch/U002-05-build.log`、`U002-05-offline-build.log`、`U002-05-export.log`、`U002-05-container-stderr.log`；JUnit 为 `U002-05-runtime-tests.xml`、`U002-05-runtime-real.xml`。不把忽略目录作为唯一长期证据，关键原始结果已保存在上述 JSON。

```powershell
$probe = (Resolve-Path docs/internal/updates/U002-semantic-intent/evidence/runtime/offline_probe.py).Path
docker run --rm --network none --read-only --cpus 2 --memory 2g --tmpfs /tmp:rw,nosuid,noexec --tmpfs /app/data:rw,nosuid,noexec --mount "type=bind,source=$probe,target=/probe.py,readonly" medipet-u002-runtime:check python /probe.py
```

本次一次性容器已退出并由 `--rm` 清理；没有新增持久验证卷、运行中的后台进程、付费模型调用或自动接受的评测基线。后续 09 需要在完成 08 后以新源码重建业务镜像，不把本次编码器探针当成整套业务验收。
