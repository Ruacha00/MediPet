# MediPet

MediPet 是一家虚构医院的门诊就诊助手。为本人或家属选择就诊人后，可以在同一个事项中查号源、准备材料、确认或取消预约，也可以获取有限症状的初步科室建议、查询已收录药品说明书、上传报告并核对原文数值。

项目使用 Python/FastAPI、Vue/Vite、Redis 和 Chroma。模型负责理解请求、选择工具和组织回答；患者归属、号源库存和预约状态由业务服务校验。医院、医生、排班、费用和联系资料均来自“明和虚构医院”演示数据。

## 可以体验什么

| 功能 | 页面上的结果 |
| --- | --- |
| 医院、科室、医生与号源查询 | 目录、可选号源及资料来源 |
| 创建和取消预约 | 先核对方案，再点击确认，取得实际办理回执 |
| 就诊准备与院内指引 | 材料清单、报到步骤、普通或无障碍文字路线 |
| 症状与科室方向 | 有来源的初步建议、缺失信息与就医提示；只使用演示医院现有科室 |
| 药品说明书信息 | 两个指定美国标签的用途、用法用量、禁忌和合并用药警示 |
| 报告文字、PDF 与图片 | 本地提取原文、整理项目和参考范围；同一事项可继续追问 |
| 本人与家属事项 | 独立患者记录，事项重命名、归档、恢复和完整历史 |
| 复合请求与运行详情 | 主辅角色、实际工具输入、结果、失败和检索来源 |
| 知识与 Skills | 导入公开资料、检索片段、查看并主动重载场景规则 |
| 监控与评测 | 运行统计、质量评分、业务断言、失败样本和候选报告 |

预约方案不会预占库存，聊天中的“确认”也不执行预约；以卡片按钮确认后的回执为准。人工导诊功能提供联系资料，不向工作人员发送消息。项目不接入真实医院，不替代医生诊断、不开处方、不评价其他医院或医生的治疗方案，也不提供支付或实时地图导航。

医疗信息范围见[健康咨询说明](docs/health-consultation.md)：药品仅收录对乙酰氨基酚 500 mg 普通片和布洛芬 200 mg 普通包衣片的两个指定美国标签，未知药物、剂型或组合不能据此判断安全。报告支持文字 PDF、扫描 PDF、PNG/JPEG/WebP，单文件最多 10 MB、PDF 最多 10 页；原件不保留，提取文字和卡片保存在所选患者的当前事项。上传预处理不请求大模型，后续聊天仍会使用模型理解与回答。

## 用 Docker Compose 启动

在仓库根目录操作，需要已运行的 Docker 与 Compose。首次复制配置；已有 `.env` 时保留自己的配置：

```powershell
Copy-Item .env.example .env
```

在本地 `.env` 填写密钥及以下配置：

```dotenv
ANTHROPIC_API_KEY=你的本地DeepSeek密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_MODEL=deepseek-flash
MEDIPET_THINKING=disabled
```

保留现有 Anthropic SDK 的 Messages 调用方式，通过 DeepSeek 的 [Anthropic 兼容入口](https://api-docs.deepseek.com/guides/anthropic_api/)发送请求。变量名表示协议配置；实际模型由 `ANTHROPIC_MODEL` 指定。`MEDIPET_THINKING=disabled` 通过 SDK 的 `extra_body` 关闭 thinking，适配现有短 JSON 输出预算；空值保留服务默认行为。实现见 [core/llm_utils.py](core/llm_utils.py)。这项配置已做短请求对照验证，完整模型质量仍需单独评测。

Compose 会优先使用终端中已有的同名变量。若当前终端继承了其他配置，先清除这些进程内旧值，使本地 `.env` 生效：

```powershell
Remove-Item Env:ANTHROPIC_API_KEY, Env:ANTHROPIC_BASE_URL, Env:ANTHROPIC_MODEL -ErrorAction SilentlyContinue
docker compose config --quiet
docker compose up --build -d
docker compose ps
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8088/api/python/health
```

首次构建会下载依赖并缓存 Chroma 客户端的默认向量模型。[API 镜像](Dockerfile)同时安装 Tesseract 及中英文 OCR 语言包，用于本地扫描报告识别。等待 API 和前端健康后，打开 [MediPet 页面](http://localhost:8088)。页面通过同源 `/api/python` 访问 Python API。

| 入口 | 默认地址 | 用途 |
| --- | --- | --- |
| MediPet | <http://localhost:8088> | 对话、知识库、评测 |
| API 文档 | <http://localhost:8000/docs> | 查看请求与响应契约 |
| Redis | `127.0.0.1:6379` | 本地数据服务 |
| Chroma | `127.0.0.1:8001` | 本地向量存储 |

宿主机端口可在 [.env.example](.env.example) 对应的 `MEDIPET_*_PORT` 变量中调整；调整后同步使用新地址。容器之间使用 `redis:6379`、`chromadb:8000` 和 `api:8000`，不使用宿主机地址。密钥只保存在被忽略的 `.env`，不进入页面运行配置或仓库。

日常停止与再次启动：

```powershell
docker compose down
docker compose up -d
```

Redis、Chroma 和 API 的运行数据使用命名卷。上述停止命令保留卷；医院事务初始化只补缺失默认数据，内置知识按固定文档 ID 更新内容并保留用户上传。Skills 目录以只读挂载提供给 API，修改宿主机文件后在“知识库 → 已加载能力”点击“重新加载”。

代码更新后仍使用同一份 Compose 重建：`docker compose up --build -d --force-recreate`。预约、完整历史和向量记忆保存在原命名卷；API 数据卷中的评测候选与接受基线也会保留。页面评测结果来自本次请求，刷新页面不会自动重载磁盘报告。

可选 Prometheus 使用同一份 Compose：`docker compose --profile monitoring up -d`，默认入口为 <http://localhost:9090>。应用内运行状态不依赖这个可选服务，外部告警默认未配置。

旧基准 `c712e87` 的容器初次启动、再次启动、四容器重建后预约及历史保留、同源代理和定向重置已有[真实验证](docs/internal/rebuild/evidence/runtime.md)。本轮医疗扩展与 OCR 镜像验收另记在 [U001 检查点](docs/internal/updates/U001-health-consultation/EXECUTION.md)，本轮证据与旧验证分开记录。日常停止无需重置数据。需要清空本项目演示记录时，先停止应用写入，再预览目标并执行：

```powershell
docker compose stop api frontend
docker compose run --rm --no-deps api python -m scripts.reset_demo
docker compose run --rm --no-deps api python -m scripts.reset_demo --execute
docker compose up -d
```

该操作删除 `medipet:*` Redis 键及 `medipet_knowledge`、`medipet_episodic`、`medipet_profiles` 三个精确集合，包含事项、预约、历史中的报告文字与卡片、已导入资料和记忆。普通启动补回两位预置患者、默认号源与 24 份知识文档。其他前缀的 Redis 数据、名单以外的 Chroma 集合、数据卷和评测报告保留。

## 演示与代码导航

完整学习资料见[教程与项目文档中心](文档+简历/文档中心.md)：包含学习路线、使用指南、业务流程、重点代码、6 张架构图与项目海报、技术亮点及带证据的简历和面试模板，内容对应当前医疗扩展实现。后续医院运营设想另行标注为未实现。

按[固定演示脚本](docs/demo-script.md)体验“孩子查号与材料 → 确认预约 → 恢复历史 → 指引 → 取消”，再核对医疗扩展的分诊、药品和报告用例。[面试说明](docs/interview-notes.md)把设计理由、代码入口、证据和取舍放在一起。当前六角色为 General、Guidance、Appointment、Escalation、Triage 和 Medication；报告由 Triage 处理，知识库共 24 份文档，Skills 共 6 组。

| 目录 | 职责 | 说明 |
| --- | --- | --- |
| `api/`、`frontend/` | HTTP 接口、患者事项和业务卡片 | [请求链路](docs/architecture.md) |
| `core/`、`agents/` | 意图识别、角色分工、工具执行 | [意图与路由](docs/intent-routing.md) |
| `hospital/` | 演示事实、方案、库存和预约事务 | [预约闭环](docs/appointments.md) |
| `health/` | 有来源的症状/药品信息、报告提取与原文范围比较 | [健康咨询](docs/health-consultation.md) |
| `mcp/`、`knowledge/` | 进程内工具管理与静态文档检索 | [工具与 RAG](docs/tools-rag.md) |
| `memory/`、`skills/` | 完整历史、有限上下文和场景规则 | [记忆与 Skills](docs/memory-skills.md) |
| `evaluation/`、`monitor/`、`tests/` | 评测、监控与分层检查 | [评测说明](docs/evaluation.md) |

## 验证状态

U001 医疗扩展已实现并部署到8088。全量后端 **684 passed、21 skipped**，前端 **35/35** 及构建通过；真实中文图片、文字/扫描/混合PDF、报告患者隔离和原预约页面已验证。完整模型候选为 **66/66意图、57/58结果项**：新增医疗场景通过，一项原无障碍追问失败已定点修复，同一两轮复测3/3通过，未重跑或改写全量分数。详见[验收汇总](docs/internal/updates/U001-health-consultation/evidence/acceptance.md)与[完整候选](evaluation/reports/live-20260916-u001/notes.md)。新旧候选均未自动接受。

以下为医疗扩展前 `c712e87` 的旧基准证据：后端 **389 passed、21 skipped**，前端 **32/32** 及构建通过；21 项需要显式启用真实组件，其独立执行证据见[存储验证](docs/internal/rebuild/evidence/storage.md)与[容器验证](docs/internal/rebuild/evidence/runtime.md)。旧版真实模型页面业务闭环和管理操作见[浏览器记录](docs/internal/rebuild/evidence/browser.md)。测试项通过数不是模型准确率，也不与独立子套结果相加。

下述旧版评测输入为 54 条意图、12 组多轮和 12 组边界场景，不包含本次医疗扩展的完整验收。当前输入与评测规则见[评测说明](docs/evaluation.md)及[用例目录](evaluation/cases/README.md)。每次运行生成 candidate，调用失败、Judge 失败、跳过和业务断言分别保留，人工复核后才能接受为基线。完整同步评测会发起多次模型请求；Nginx 为 `/api/python/eval/run` 设置 1800 秒等待。

[首份真实完整候选](evaluation/reports/live-20260916-full/notes.md)已保存，运行编号 `0f457b4d311941aab9ad63a16dad216d`。其失败与原始分数保留，因评测漏报及 Judge 背景缺本轮事实等审查限制，未接受为基线。计分与背景修正后的完整容器报告另存新目录，不能据两次不同实现的分数直接声称提升。

[完整容器报告](evaluation/reports/live-20260916-container/notes.md)已保存：54/54 意图预测、24/24 业务断言和24/24质量项通过，HTTP200耗时170.344秒，调用异常、Judge失败及跳过均为0。仍保留“大厅”简称查询时要求补充完整地点名称的行为。报告保持 candidate，等待人工复核；这些固定样本结果不代表任意输入的准确率，也不构成两次不同实现之间的提升证明。
