# B01 后端源码导入与命名适配记录

日期：2026-09-16。状态：B01 已完成原样导入、品牌/项目参数适配、模块导入与原机制测试；应用启动和业务场景验收由后续 issue 负责。
对应 [B01](specs/S01-baseline/issues/B01.md)，来源与边界以[总计划](../../../IMPLEMENTATION_PLAN.md) §2.2 为准。

## 来源与导入方法

- 来源仓库：EchoMind；固定提交 `3f5a163a9922799c28087f4d8519b6c692cd5d05`，已通过 Git 对象查询确认存在。
- 目标分支：`refactor/medipet-scenario`；仅向当前 MediPet 重构工作树写入。
- 从固定提交列出 B01 范围内跟踪文件，再逐文件读取 Git blob 原始字节；未复制源工作区文件。
- 写入前确认全部目标文件不存在，写入后逐文件核对 SHA-256 与 Git blob 相同。
- 项目外指纹清单覆盖的 13 个导入文件全部吻合；另 4 个 Skill 文档直接与固定提交 blob 核对。
- 导入前后源工作区状态一致。现有根 `.git`、许可证、工作约定、计划、README、运行配置和其他执行者文件未修改。
- 本文不收录本地源码定位清单、外部备份或个人路径。

## 导入文件

共 17 个文件。此表记录本次原样导入时的 SHA-256；后续命名适配产生的差异应另行记录，不能继续声称适配后逐字节相同。

| 目标路径（与来源相同） | 原始 SHA-256 |
| --- | --- |
| `agents/agent_orchestrator.py` | `f7822a67bd71a19dc875f7ff207b1329fdf6da82e1a560a439a5f1e3d8db6879` |
| `agents/tools.py` | `30eaf765c5d6bf5fb6e8c1550fa1cd508361f0566f27715d87fbfe0c26faebf2` |
| `api/main.py` | `3b01c3a771c708eb4ea1dcbc7a8ff5d8d30fb74cdce2181da37efba21d60bcff` |
| `core/intent_recognizer.py` | `4a84a233a86d0575e3188aefe8e4850b9b9176c51977141a57ab6f2e5e17ba98` |
| `core/llm_utils.py` | `b0bf4cc194787b059a02c2049a773dceb1ff92df95f5d7d7bdc492ba355e93e7` |
| `core/skill_loader.py` | `26618d02cde5592955bd45b7a6752e6fbfa3e06de76ed325658f5257f142aaaf` |
| `evaluation/evaluator.py` | `0c320755e171987f49d42925c89326036201a49d5a93f073e1df4abb5bcfdc60` |
| `mcp/knowledge_base.py` | `5869466a02ebfe3115f5107a75a51832a5dee9a56131036a31eef3d656373750` |
| `mcp/tool_manager.py` | `f76b914a3565ddc44011ad9910cd99bcd054813d4845b724f5a8221bb8b3d09c` |
| `memory/conversation_memory.py` | `891e48cf92bb5b09d8fa1cf239cc30d05939f82da265033aa2b574e2e7c4f079` |
| `monitor/performance_monitor.py` | `40f0068eed4386efa591717915a7c8f68bf5d53c7d166c88d95d066290aa3d62` |
| `requirements.txt` | `bba3bdc81532cc6f95e1e4a0bdf8109ec5cb8f1012fbb131bc119360fe1de3c9` |
| `skills/README.md` | `3c86fc493c3c73c79e1f01bb85067c874fba59b61f21bb7e9a1bd381235c9aa5` |
| `skills/billing_support/SKILL.md` | `60baeba56cb91d2113d529f999f2d14da67425ba11efdc5175c9cf90fbb7223f` |
| `skills/general_customer_service/SKILL.md` | `840a45efc7e35abc75b97c37bd47703de53a73ec8844ca29f342f5babddbe037` |
| `skills/technical_support/SKILL.md` | `7e730fb06966314f6530f478debe81bd2a854501f168c5406465df674dcb9cb6` |
| `tests/test_agent_orchestrator.py` | `7b1f32b700363b6a2067e1238a8e563ff1032b11724a5999e609cf32ea80756f` |

## 排除与交接

- 未导入源提交中跟踪的 `.env`、`.idea/`、`data/chroma/chroma.sqlite3`；未读取或展示环境密钥值。
- 未导入源目录的 Git 元数据、虚拟环境、依赖、运行缓存、日志、数据库或未提交部署实验。
- 后端 `Dockerfile`、`docker-compose.yml`、`config/` 和部署脚本不属于本轮写入范围；B03 按固定提交及目标启动方案另行接入。
- 源根 README、wiki、旧更新说明和简历材料不导入；对外说明由后续对应 issue 编写。
- 三组旧业务 Skill 暂作原机制基准保留；K03 负责替换为四组医院 Skill 并去掉旧有效内容。

## 已完成的 B01 品牌与入口适配

以下位置已在当前工作树索引建立和影响核对后逐处修改；行号对应原始导入版本。本轮没有重命名 Python 类、函数或方法。

| 位置 | 已修改内容 | 保留边界 |
| --- | --- | --- |
| `api/main.py:2,39,182,188,193,619,670` | 模块说明、横幅、日志、FastAPI 标题及 CLI 显示统一为 MediPet | HTTP 端点、SDK 和调用算法不变；横幅补一格以维持对齐 |
| `api/main.py:93,96,627,628` | `MEDIPET_SKILLS_DIR`、`MEDIPET_SKILLS_MAX_PROMPT_CHARS` | B03 负责环境示例；默认目录与上限不变 |
| `agents/agent_orchestrator.py:421` | GeneralAgent 提示词中的项目品牌改为 MediPet | 角色业务内容由 A02 适配 |
| `agents/agent_orchestrator.py:596,597,658,681` | `MEDIPET_COMPOSER_MAX_TOKENS`、`MEDIPET_COMPOSER_TEMPERATURE`、`MEDIPET_TOOL_TRACE_MAX`、`MEDIPET_<ROLE>_MODEL` | 默认值、模型协议、工具循环和整合逻辑不变 |
| `core/skill_loader.py:2,169,187` | 模块说明、注入提示和加载日志改为 MediPet | 加载/匹配算法不变 |
| `mcp/knowledge_base.py:64` | 知识库集合描述改为 MediPet | 默认内容/专用集合由 K02 负责 |
| `requirements.txt:1` | 依赖清单标题改为 MediPet | 固定依赖版本保留 |
| `skills/README.md:1,3`、三组 `SKILL.md:3,13` | 文档品牌、项目配置前缀和自称统一 | 完整医院内容替换留 K03 |

HTTP 入口仍是 `api.main:app`，`api/main.py` 的 `__main__` 分支支持原 CLI 与 Uvicorn；本 issue 未启动 HTTP 服务。`/chat`、`/health`、`/skills`、`/skills/reload`、知识/检索/监控/trace/评测路由均原样保留。未发现以来源项目命名的 Python 类或函数，因此没有制造符号重命名。`ANTHROPIC_API_KEY`、`ANTHROPIC_MODEL`、`ANTHROPIC_BASE_URL` SDK 参数保持；用户选择的模型与兼容地址由 B03 环境配置接入。

## 修改前影响核对

- 绑定仓库及工作树：`D:/Projects/Agent Learn/Project/MediPet-Rebuild`；索引于 `2026-09-16T06:08:05.630Z` 建立，对应提交 `d0d2bdb2a30673035ff1d7dd7db8f0aa9f50a79e`，含本轮导入文件，1396 节点、2584 边。
- 每次 GitNexus 查询均显式使用上述绝对路径，未按同名仓库默认解析。图查询可用；FTS 构建失败及流程提取分支限额保留为 B04 修复/核对事项，不把调用图当成穷尽所有流程。
- `_log_loaded_skills`：HIGH，直接调用方为 `load`，间接涉及 `lifespan`、`_cli`、`reload`；已在改动前报告。仅修改日志横幅，加载/重载冒烟验证通过。
- `ResponseComposer.compose`：HIGH，直接调用方为 `run_parallel` 及原整合降级测试，间接涉及 `run`、`_cli`；已在改动前报告。仅调整两个环境变量前缀，假客户端确认参数覆盖生效。
- `_make_agent`、`_cli`、GeneralAgent、ResponseComposer、AgentOrchestrator、KnowledgeBase 查询均检查了直接引用。GeneralAgent 动态分发给出 lower-bound，结合当前池初始化与 `_build_system_prompt` 调用核实。
- `lifespan`、`prompt_for`、两个构造方法及 API 文件级查询给出 UNKNOWN，未当成零影响：源码确认 FastAPI 的 `lifespan=lifespan`、HTTP/CLI 实例化、BaseAgent 与 ResponseComposer 中两处 `prompt_for` 调用、CLI 主入口绑定。
- 不修改符号名称、签名和调用关系；提交前完整变更分析及索引刷新由主代理按 B04/仓库交付要求继续处理，本记录不宣称其已通过。

## 后续场景符号与配置清单（不在本阶段修改）

- A01：`IntentCategory` 的旧技术/账单类别、模板、关键词、实体与分类提示词。
- A02：`TechnicalAgent`/`BillingAgent`、`AgentType.TECHNICAL/BILLING`、池初始化、路由/打分/协作及测试引用；须执行符号重命名分析。
- A03：`technical_tools`、`billing_tools`、错误码/诊断/账单字段等旧工具；保留 AgentToolSpec、make_tool 和共享 RAG 扩展点。
- A04：成功工具 trace 和本轮结果透传；A05：急症固定中断与人工导诊。
- K02：`KnowledgeBase.COLLECTION_NAME` 当前为 `knowledge_base`，以及默认旧业务文档；K03：三组基准 Skill 和说明。
- M03：MemoryManager 当前 `episodic`、`user_profile` 集合及身份作用域；B03 提供独立运行数据空间，M03 再完成场景隔离。
- V01/V02：旧业务评测输入和 Judge 提示；现有评测器、监控和测试机制保持原样。

## 验证结果与交接

- 导入时验证：固定提交存在、17 个文件与来源 blob 完全一致、其中 13 个命中外部固定指纹、源工作区状态未变化、实际工作分支正确。
- 适配后逐文件再次读取固定 Git blob 比对：17 个文件中 9 个仅有已核对的品牌/项目前缀及横幅间距差异，另 8 个保持原始字节；依赖版本、默认模型、算法和协议均未改写。
- `.venv/Scripts/python.exe -m pytest -q tests/test_agent_orchestrator.py`：**10 passed in 0.34s**，覆盖原角色契约、白名单、工具往返、路由和整合降级。这是假模型机制回归，不是医院业务或实际模型验证。
- 在同一虚拟环境执行 11 个后端模块导入成功：API、编排、工具、意图、LLM 工具、Skill 加载、记忆、知识库、工具管理、监控和评测；API 标题为 MediPet。导入没有运行 lifespan 或连接真实存储。
- 无网络冒烟通过：3 个基准 Skill 加载/重载与 MediPet 提示注入；假模型捕获 Composer 的新上限/温度参数，构造替身验证 trace 上限和角色模型覆盖。均使用进程内临时参数，不写入密钥或运行配置。
- B01 范围执行大小写不敏感的旧品牌扫描，匹配数为 0；内部来源记录按计划保留来源名。旧客服角色/内容为基准阶段保留，交 A/K/V 组转换。
- 未执行：真实 Redis/Chroma 集成、API 服务启动/健康检查、实际模型演示、容器验收、提交与推送；这些不作为 B01 的已通过证据。
- B01 已完成并交还 API 等文件；B03 可继续环境参数与运行接线，B04 统一处理索引/基准验收。
