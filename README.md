# MediPet

MediPet 是一家虚构医院的门诊就诊助手。用户为本人或家属建立独立事项后，可以查询医院与号源、准备就诊材料、确认或取消预约，也可以获取有限的症状分诊信息、查询已收录药品标签、上传报告并核对原文数值。

项目使用 Python/FastAPI、Vue/Vite、Redis 和 Chroma。模型负责理解请求、选择工具和组织回答；患者归属、号源库存、预约状态和幂等回执由业务服务校验。医院、医生、排班、费用和联系资料均来自“明和虚构医院”演示数据。

## 可以体验什么

| 功能 | 页面上的结果 |
| --- | --- |
| 医院、科室、医生与号源查询 | 目录、可选号源及资料来源 |
| 创建和取消预约 | 先核对方案，再点击卡片确认，取得服务端办理回执 |
| 就诊准备与院内指引 | 材料清单、报到步骤、普通或无障碍文字路线 |
| 症状与科室方向 | 有来源的初步方向、缺失信息和就医提示 |
| 药品标签信息 | 已收录药品的用途、用法用量、禁忌和相互作用警示 |
| 报告文字、PDF 与图片 | 本地提取原文、整理项目和参考范围，同一事项可继续追问 |
| 本人与家属事项 | 独立记录、事项重命名、归档、恢复和完整历史 |
| 知识与 Skills | 检索或导入公开资料，查看并主动重载场景规则 |
| 运行与评测 | 查看工具轨迹、来源、运行统计、失败样本和候选报告 |

首页以就诊操作为主，患者、事项、消息、报告上传和预约卡片保持在同一工作区。知识库、Skills、监控、评测和 API 文档位于“管理与调试”入口，进入对应页面时才读取管理数据；进入评测页不会自动调用模型。

预约方案不会预占库存，聊天中的“确认”不会执行预约；只有卡片按钮会调用确认接口。若确认请求中断，页面会暂停重复提交，并通过完整历史核对服务端回执。人工导诊只提供联系资料，不向工作人员发送消息。

项目不接入真实医院，不替代医生诊断、不开处方、不评价其他医院或医生的诊疗方案，也不提供支付或实时地图导航。药品范围和报告边界见[健康咨询说明](docs/health-consultation.md)：当前药品资料仅覆盖两个指定公开标签，未知药物、剂型或组合不能据此判断安全；报告原件不保留，提取文字和卡片保存在所选患者的当前事项。

## 用 Docker Compose 启动

以下宿主机命令使用 Windows PowerShell。在仓库根目录操作，需要已启动的 Docker 引擎、Docker Compose 2.24+ 和 Python 3.12。首次复制配置；已有 `.env` 时保留自己的文件：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

在本地 `.env` 填写模型配置。以下密钥只是占位内容：

```dotenv
ANTHROPIC_API_KEY=填写自己的 DeepSeek API Key
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-flash
MEDIPET_THINKING=disabled
```

代码保留 Anthropic SDK 的 Messages 调用协议，通过 DeepSeek 的 [Anthropic 兼容入口](https://api-docs.deepseek.com/guides/anthropic_api/)发送请求。变量名表示协议，实际模型由 `ANTHROPIC_MODEL` 指定；密钥只保存在被忽略的 `.env`，不会进入页面配置或仓库。

### 准备中文意图模型

意图向量默认使用固定版本的 `BAAI/bge-small-zh-v1.5` ONNX 模型。字符哈希是配置中明确指定的降级后端；默认语义后端初始化失败时会自动进入该降级，并在 `/health` 暴露实际状态。运行时不会下载模型；第一次构建镜像前，在独立 Python 环境中准备并核验本地资产：

```powershell
py -3.12 -m venv .scratch/intent-model-build
.\.scratch\intent-model-build\Scripts\python.exe -m pip install -r requirements-embedding-build.txt
.\.scratch\intent-model-build\Scripts\python.exe scripts/prepare_intent_embedding.py download
.\.scratch\intent-model-build\Scripts\python.exe scripts/prepare_intent_embedding.py export --python .scratch/intent-model-build/Scripts/python.exe --evidence .scratch/intent-model-export.json
.\.scratch\intent-model-build\Scripts\python.exe scripts/prepare_intent_embedding.py verify --smoke
```

`download`、构建环境依赖安装和后续镜像构建首次执行时需要联网，并需为模型源文件、PyTorch 导出环境和镜像预留数 GB 磁盘；已有完整固定缓存后，`export` 与 `verify` 可以离线执行。模型资产保存在被忽略的 `data/models/intent/bge-small-zh-v1.5`，身份、固定 revision、文件哈希和预处理方式记录在[模型清单](config/intent_embedding_model.json)。生产镜像只包含 ONNX Runtime，不安装 PyTorch 或 Transformers；镜像构建会再次校验资产。

### 启动服务

Compose 会优先使用当前终端中已有的同名变量。若终端继承了其他模型配置，先清除这些进程内旧值，让本地 `.env` 生效：

```powershell
Remove-Item Env:ANTHROPIC_API_KEY, Env:ANTHROPIC_BASE_URL, Env:ANTHROPIC_MODEL -ErrorAction SilentlyContinue
docker compose config --quiet
docker compose up --build -d --wait
docker compose ps
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8088/api/python/health
```

首次构建会下载 Python、Node 和系统依赖，并缓存 Chroma 客户端的默认向量模型；因此首次构建不是离线过程。API 镜像包含 Tesseract 及中英文 OCR 语言包，用于本地扫描报告识别。上述健康检查只证明应用和本地依赖已就绪，不证明 DeepSeek 密钥、余额、模型权限或一次实际模型调用成功；实际演示会访问外部模型并产生相应调用。服务健康后，打开 [MediPet 页面](http://localhost:8088)。

| 入口 | 默认地址 | 用途 |
| --- | --- | --- |
| MediPet | <http://localhost:8088> | 就诊工作区及管理入口 |
| API 文档 | <http://localhost:8000/docs> | 请求与响应契约 |
| Redis | `127.0.0.1:6379` | 事项、历史、预约与工作记忆 |
| Chroma | `127.0.0.1:8001` | 知识、情景记忆与画像向量 |

宿主机端口可通过 [.env.example](.env.example) 中的 `MEDIPET_*_PORT` 调整。容器之间使用 `redis:6379`、`chromadb:8000` 和 `api:8000`，不使用宿主机地址。

日常停止、再次启动和代码更新：

```powershell
docker compose down
docker compose up -d
docker compose up --build -d --force-recreate
```

Redis、Chroma 和 API 运行数据使用命名卷；`docker compose down` 不删除卷。预约、完整历史、向量记忆、评测候选和接受基线会在重建后保留。Skills 以只读目录挂载，修改文件后进入“管理与调试 → 知识与场景规则”，点击“重新加载”。页面刷新不会自动从磁盘恢复上一次评测结果，也不会自动运行评测。

可选 Prometheus 使用同一份 Compose：

```powershell
docker compose --profile monitoring up -d
```

默认地址为 <http://localhost:9090>。应用内运行状态不依赖这个可选服务，外部告警默认未配置。

### 重置演示数据

日常停止不需要重置。需要清空本项目演示记录时，先停止应用写入，再预览目标并显式执行：

```powershell
docker compose stop api frontend
docker compose run --rm --no-deps api python -m scripts.reset_demo
docker compose run --rm --no-deps api python -m scripts.reset_demo --execute
docker compose up -d
```

该操作删除 `medipet:*` Redis 键及 `medipet_knowledge`、`medipet_episodic`、`medipet_profiles` 三个精确集合，包括事项、预约、报告文字与卡片、已导入资料和记忆。普通启动会补回预置患者、默认号源与内置知识；其他 Redis 前缀、名单以外的 Chroma 集合、数据卷和评测报告保留。

## 演示与代码导航

完整学习资料见[教程与项目文档中心](文档+简历/文档中心.md)，包括学习路线、完整使用指南、业务流程、重点代码、架构图、技术亮点以及带证据的简历与面试模板。

按[固定演示脚本](docs/demo-script.md)体验“查号与材料 → 选择号源 → 显式确认 → 恢复历史 → 就诊指引 → 取消预约”，再核对分诊、药品和报告用例。[面试说明](docs/interview-notes.md)将设计理由、代码入口、验证证据和取舍放在一起。

| 目录 | 职责 | 进一步阅读 |
| --- | --- | --- |
| `api/`、`frontend/` | HTTP 接口、就诊工作区和业务卡片 | [请求链路](docs/architecture.md) |
| `core/`、`agents/` | 三路意图融合、角色路由和工具执行 | [意图与路由](docs/intent-routing.md) |
| `hospital/` | 演示事实、方案、库存和预约事务 | [预约闭环](docs/appointments.md) |
| `health/` | 分诊、药品资料、报告提取与原文范围比较 | [健康咨询](docs/health-consultation.md) |
| `mcp/`、`knowledge/` | 工具管理、查询改写、检索和重排 | [工具与 RAG](docs/tools-rag.md) |
| `memory/`、`skills/` | 完整历史、工作/情景记忆、画像和规则 | [记忆与 Skills](docs/memory-skills.md) |
| `evaluation/`、`monitor/`、`tests/` | 评测、监控和分层验证 | [评测说明](docs/evaluation.md) |

## 验证状态

验证按源码测试、真实存储/OCR、浏览器业务流程和实际模型评测分层记录，数字不跨测试套相加。

- **U001 · 医疗扩展：**后端全量 **684 passed、21 skipped** 和前端 **35/35** 记录在最后一次 Guidance 提示调整之前；该调整后相关 Agent/医院流程 **119** 项定向回归通过。真实中文图片、文字/扫描/混合 PDF、报告患者隔离和预约页面均有独立验证。实际模型候选为 **66/66 意图、57/58 结果项**；失败的一项在定点修复后原样两轮 **3/3**，没有重跑全量，因此不能写成 58/58。详见 [U001 验收](docs/internal/updates/U001-health-consultation/evidence/acceptance.md)。
- **U002 · 中文语义向量：组件接入完成，方案验收未通过。**真实 ONNX、独立编码器探针容器的只读/断网推理和回放链路通过。冻结留出集中，单路语义覆盖和低重合表现高于字符哈希，但预声明的 LLM 故障接受准确率为 **69/84（82.143%）**，没有达到 90% 门槛；完整业务容器闭环尚未执行。该轮候选不能作为“整体准确率提升”证据，也不能继续用同一留出集调参。详见 [U002 实施结果](docs/internal/updates/U002-semantic-intent/evidence/calibration/implementation.md)。
- **U003 · 前端可用性：**前端 **82/82** 项测试及生产构建通过；隔离 API/真实存储回归为 **107 passed、5 skipped**，另有 4 项真实 Tesseract OCR 补验通过。360、768、1440 宽度已在开发预览核对；预约和取消浏览器流程使用确定性内存 HTTP 夹具，不是实际模型或真实 Redis 验证。浏览器完整文件上传、真实 200% 缩放和移动软键盘仍保留为环境验收项；源码尚未替换当前 8088 容器部署。详见 [U003 验收](docs/internal/updates/U003-frontend-usability/evidence/acceptance.md)。

每次完整评测都会生成 candidate，并分别保留调用失败、Judge 失败、跳过和业务断言；只有人工复核后才能接受为 baseline。当前报告没有因代码测试通过而自动接受。固定数据集的结果不代表任意医疗输入的准确率，也不能把不同实现、不同范围或定点复测的数字直接比较为提升。
