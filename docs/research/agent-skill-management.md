# Agent 应用中的 Skill 与 Tool 管理模式研究

调研日期：2026-08-30

## 结论摘要

主流框架正在形成一个相当稳定的分层：

- **Skill 是可发现、按需加载的指令与资源包**。OpenAI、Anthropic 和 Google ADK 都采用或兼容 Agent Skills 的 `SKILL.md` 结构；名称与描述用于发现，正文和资源只在需要时加载。Skill 可以附带脚本，但这意味着它应按“特权指令和代码”审核，而不是当作普通业务数据。
- **Tool 是可执行能力**。LangChain、AutoGen、Semantic Kernel、Google ADK 和 OpenAI Agents SDK 的基本做法都是由应用代码注册函数，或从受信任的 MCP/OpenAPI 服务发现工具；模型只看到工具描述和 JSON Schema，并不因此获得注册任意代码的权力。
- **动态能力管理通常是“从已注册/已信任集合中筛选、绑定和按需发现”**，而不是让终端用户上传任意 Python/JavaScript 并在 API 进程中执行。OpenAI 官方甚至明确建议不要向消费者开放任意 Skill 目录，并要求高影响写操作经过审批。
- **版本、发布与运行时装载应分开**。OpenAI、Anthropic 的托管 Skills 都有不可变版本或版本指针；Google ADK 支持从文件系统、代码或任意后端 `Source` 加载；LangSmith 的 Assistant 配置也通过新版本、激活和回滚管理。适合 MediPet 的模型是 `draft -> reviewed -> published -> retired`，运行中的会话固定到一个已发布版本。
- **医疗安全策略不能降格为普通 Skill**。它可以采用相同的版本化内容模型并允许医院管理员修改，但必须置于平台级受保护命名空间，禁止删除或由普通 Skill 覆盖，修改需要双人复核、评测、审计和显式发布。模型可以生成辅助风险判断，流程必须把结果送给医院人员裁决，并在未裁决前禁止高影响动作。

因此，MediPet 的 Q13 应采用 **Agent Skills 风格的声明式 Skill 包 + 受信任 Tool Registry**，而不是只做一个随意的 prompt 表，也不应允许后台上传可执行代码。Q14 首期应只允许医院系统管理员查看、启停和绑定代码中预注册的 Tool；后续若开放 HTTP/MCP/OpenAPI Tool，也应作为独立的受管连接器发布流程，而不是 Skill 内容的一部分。

## 术语边界

本报告使用以下区分：

- **Skill**：描述“如何完成一类任务”的说明、流程、示例和参考资源；可通过元数据发现，并按需加载完整内容。
- **Tool**：实际读取数据或执行动作的函数/API/MCP 能力，具有名称、描述、输入输出 Schema、凭据和副作用等级。
- **Agent/Assistant 配置**：将模型、系统指令、Skills、Tools、审批策略等组合成一个可运行版本。

不同平台会使用 Plugin、Workbench、Toolset 等词，但并不都等价于 Agent Skill。尤其在 Semantic Kernel 中，Plugin 本质上是函数集合；在 AutoGen 中，Workbench 是共享状态和资源的一组 Tools。

## 框架对比

| 平台 | Skill 形态 | Tool 形态 | 动态发现/装载 | 版本/发布 | 权限与安全边界 |
| --- | --- | --- | --- | --- | --- |
| OpenAI API / Agents SDK | Agent Skill 是包含 `SKILL.md` 的版本化文件包，可含参考资料与脚本；兼容 Agent Skills 标准 | Python 函数、托管 Tool、Agent-as-Tool、MCP Tool | Skill 先暴露名称/描述/路径，模型需要时读取正文；Tool Search 与 MCP 支持延迟发现和过滤 | Skills API 有版本、`default_version`、`latest_version` 和显式版本引用 | Skill 被视为特权指令/代码；官方反对消费者任意选择开放 Skill 目录；敏感 Tool 支持暂停、审批和恢复 |
| Anthropic Claude | `SKILL.md` + 支持文件；元数据、正文、资源三级渐进加载 | Claude Code/SDK Tools 与 MCP 分离管理 | 项目、用户、插件目录自动发现；托管 Agent 可绑定自定义或官方 Skill | API Skill 支持完整快照版本；托管 Agent 也是可复用、版本化配置 | Workspace 是 API Skill 隔离边界；官方提醒仓库 Skill 会在会话开始时加载，具有 Bash/网络能力时必须审查 |
| Google ADK | 实验性 `SkillToolset`，兼容 Agent Skills；支持内联、文件系统和自定义 `Source` | 函数自动包装为 `FunctionTool`；另有 Toolset、MCP、OpenAPI | L1 元数据、L2 指令、L3 资源/脚本渐进加载；`Source` 可由数据库实现 | 官方 Skill API 本身未提供完整发布工作流；应由应用层补足 | Skill 可调用 `run_skill_script`，因此脚本来源必须受信任；动作确认作为 Tool 层机制 |
| LangChain / LangGraph | 核心 OSS 没有与 Agent Skills 完全等价的一等管理资源；通常用 prompt/config/graph 表达流程 | `@tool` 函数、`ToolNode`、MCP adapters | Middleware 可按认证、角色、状态筛选 Tools，也可在运行时加入 MCP Tools | LangSmith Deployment 的 Assistant 配置可版本化、激活和回滚，但不是 OSS Skill Registry | HITL middleware 在 Tool 执行前中断；检查点持久化后才能安全恢复；MCP interceptor 可做认证、限流和阻断 |
| Microsoft AutoGen | 没有同等的一等 `SKILL.md` 管理模型 | `FunctionTool`、内置 Tool、MCP Tool；Workbench 是共享状态/资源的 Tool 集合 | MCP Workbench 运行时列举工具，静态 Workbench 从组件配置恢复 | Workbench 可保存/恢复状态，但官方未提供通用 Skill 发布目录 | 官方警告只连接受信任 MCP Server，尤其 stdio 会在本机执行命令；审批需由 Intervention/HITL 流程显式实现 |
| Microsoft Semantic Kernel | Plugin 是语义描述的函数集合，不是纯指令 Skill | Native code、OpenAPI 或 MCP 导入的 Kernel Functions | Kernel 注册 Plugin；Function Choice filters 决定本次向模型公布哪些函数 | SDK 未提供通用 Plugin 版本仓库，通常由应用/部署层负责 | Invocation Filters 可检查权限、审批并阻止函数执行；OpenAPI/MCP 导入仍应位于可信连接边界 |

## 一手资料观察

### OpenAI

OpenAI 将 Skill 定义为“版本化文件包 + `SKILL.md` manifest”，用于编码流程和约定，并与开放的 Agent Skills 标准兼容。托管 API 支持目录或 zip 上传，运行时可固定具体版本；平台只先把名称、描述和路径加入上下文，模型选中后再读取完整说明。[OpenAI Skills 指南](https://developers.openai.com/api/docs/guides/tools-skills)、[OpenAI Skills API](https://developers.openai.com/api/reference/python/resources/skills/methods/create)

这里有一个对 MediPet 很重要的细节：OpenAI 把 Skill 指令放在用户级上下文，而不是 system prompt；因此普通 Skill 不能承担不可覆盖的最高优先安全策略。官方同时把 Skills 视为可能影响规划、工具使用和命令执行的特权内容，要求开发者审核，避免让消费者从开放目录任意挂载，并对写入或高影响动作加入审批和策略检查。[OpenAI Skills 风险与安全](https://developers.openai.com/api/docs/guides/tools-skills#risks-and-safety)

Tools 则是另一层。Agents SDK 支持函数 Tool、托管 Tool、Agent-as-Tool、运行时 Tool 与 MCP；MCP 可以静态或动态过滤。敏感函数、Shell、Apply Patch、Agent-as-Tool 和 MCP Tool 都可以进入统一的 human-in-the-loop 中断流程，序列化状态后再审批和恢复。[OpenAI Agents SDK Tools](https://openai.github.io/openai-agents-python/tools/)、[OpenAI Agents SDK MCP](https://openai.github.io/openai-agents-python/mcp/)、[OpenAI Agents SDK HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)

### Anthropic

Anthropic 的 Agent Skills 同样采用 `SKILL.md` 与可选脚本、模板、参考资源，并明确描述了三级渐进披露：元数据常驻、说明触发时加载、资源和代码按需加载。Claude Code 可从项目、用户与插件目录发现 Skills；Claude Managed Agents/Skills API 则支持 Workspace 范围的上传、绑定和版本化。[Anthropic Agent Skills 概览](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)、[Claude Agent SDK Skills](https://code.claude.com/docs/en/agent-sdk/skills)

Anthropic 的自定义 Skill 版本是完整快照而非增量；API Workspace 是共享和隔离边界，任何同 Workspace 的 API key 都能读取、调用和删除 Skills，多租户应用应拆分 Workspace。托管 Agent 本身也将模型、system prompt、Tools、MCP Servers 和 Skills 组合成一个可复用的版本化配置。[Anthropic Skills API 管理](https://platform.claude.com/docs/en/build-with-claude/skills-guide)、[Claude Managed Agent 配置](https://platform.claude.com/docs/en/managed-agents/agent-setup)

### Google ADK

Google ADK 已提供实验性的 `SkillToolset`。它采用 Agent Skills 结构，并明确分为 L1 元数据、L2 指令、L3 参考资料/资产/脚本；系统会要求模型先 `load_skill`，需要资源或脚本时再调用对应工具。Skills 可以直接在代码定义、从文件系统加载，或通过自定义 `Source` 从数据库等存储读取，所以“后台数据库管理、运行时按需加载”在技术上是自然延伸。[Google ADK Skills](https://adk.dev/skills/)

ADK 的普通 Tool 仍由函数、Toolset、MCP 或 OpenAPI 提供。Python 函数加入 Agent 的 tools 列表后被自动包装，框架根据函数签名、类型和 docstring 生成 Schema；`ToolContext` 由运行时注入且不会暴露给模型。[Google ADK Function Tools](https://adk.dev/tools-custom/function-tools/)、[Google ADK MCP Tools](https://adk.dev/tools-custom/mcp-tools/)

### LangChain / LangGraph

LangChain 把 Tool 定义为具有明确输入输出的可调用函数，通常用 `@tool` 注册；LangGraph 的 `ToolNode` 执行模型生成的 Tool Calls。Middleware 可以根据会话状态、Feature Flag 或用户角色，在每次模型调用前过滤已注册 Tools；MCP adapters 可以从多个 MCP Server 加载 Tools，interceptor 则能注入认证信息、限流、修改请求或直接阻断调用。[LangChain Tools](https://docs.langchain.com/oss/python/langchain/tools)、[LangChain Context Engineering](https://docs.langchain.com/oss/python/langchain/context-engineering)、[LangChain MCP](https://docs.langchain.com/oss/python/langchain/mcp)

Human-in-the-loop middleware 在模型生成调用后、Tool 执行前检查策略，可批准、编辑或拒绝；恢复依赖 LangGraph checkpointer。LangSmith Deployment 另外提供 Assistant 配置版本，可以通过 API/UI 管理 prompts、models、tools 等并切换活动版本，但这是部署产品能力，不是 OSS LangGraph 的通用 Skill Registry。[LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)、[LangSmith Assistants](https://docs.langchain.com/langsmith/assistants)

### Microsoft AutoGen 与 Semantic Kernel

AutoGen 的 Tool 是可执行代码，`FunctionTool` 根据描述和类型标注生成 JSON Schema；Workbench 把共享状态和资源的 Tools 组合起来，MCP Workbench 可在运行时列举并调用远端工具。官方明确警告 MCP 尤其是 stdio 连接会执行本地命令，只应连接受信任 Server。[AutoGen Tools](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/components/tools.html)、[AutoGen Workbench](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/components/workbench.html)、[AutoGen MCP Reference](https://microsoft.github.io/autogen/stable/reference/python/autogen_ext.tools.mcp.html)

Semantic Kernel 的 Plugin 是一组带语义描述的函数，可从 Native code、OpenAPI 或 MCP 导入；Kernel 是管理服务与 Plugins 的依赖注入容器。Function Choice filters 决定本次向模型公布哪些 Plugin/Function，Invocation Filters 则能在执行时验证权限、请求人工同意并阻止调用。[Semantic Kernel Plugins](https://learn.microsoft.com/en-us/semantic-kernel/concepts/plugins/)、[Semantic Kernel Function Choice](https://learn.microsoft.com/en-us/semantic-kernel/concepts/ai-services/chat-completion/function-calling/function-choice-behaviors)、[Semantic Kernel Task Automation Approval](https://learn.microsoft.com/en-us/semantic-kernel/concepts/plugins/using-task-automation-functions)

## 对 MediPet 的建议契约

### 1. Skill 数据模型

采用 Agent Skills 的最小兼容核心，但由 MediPet 增加治理元数据：

- `skill_id`：稳定 ID；名称可改，ID 不变。
- `name`、`description`：用于发现；遵循小写 kebab-case 与长度限制。
- `instructions_markdown`：不包含密钥，不直接赋予权限。
- `resources`：首期只允许 Markdown、JSON Schema、模板等非执行内容。
- `tool_bindings`：引用 Tool Registry 中的稳定 `tool_id`，不内嵌实现。
- `status`：`draft | in_review | published | retired`。
- `version`：发布后不可变；修改产生新草稿版本。
- `scope`：医院/院区/科室范围，未来与医院身份系统绑定。
- `risk_level`、`required_approvals`：用于运行时策略，而非只展示给模型。
- `created_by`、`reviewed_by`、`published_by`、时间戳与变更说明。

导入时接受标准 `SKILL.md` 和资源目录；如果包中含 `scripts/` 或其他可执行内容，首期应拒绝导入或隔离为“不可运行、待安全审核”，不能因为格式合法就执行。

### 2. Tool Registry

首期只注册代码中实现、测试并部署的可信 Tools。后台可以：

- 查看 Tool 名称、Schema、版本、数据来源、读写属性和副作用等级；
- 为特定 Skill 绑定/解绑；
- 按环境、医院、角色启停；
- 配置审批策略、超时、限流和审计标签；
- 不允许编辑实现、不允许上传 Python/JavaScript、不允许填写任意 URL 后立即可用。

远期新增外部 Tool 时，优先采用受管 MCP 或经过校验的 OpenAPI 连接器，并增加域名/IP allowlist、SSRF 防护、TLS、凭据 Vault、最小权限身份、Schema 快照、超时/重试/幂等、响应大小限制、出站审计以及人工发布。连接失败必须显式失败，不能自动降级到 fake 数据；fake adapter 只能由非生产环境显式选择。

### 3. 运行时装载

建议执行顺序：

1. 根据租户、角色、环境和会话阶段取得可用的已发布 Skill 元数据。
2. 只向模型提供名称、描述和稳定 ID。
3. 模型选择 Skill 后，运行时加载该会话固定版本的完整指令。
4. 运行时求交集：`Skill 绑定 Tools ∩ 租户启用 Tools ∩ 当前角色权限 ∩ 当前流程阶段`。
5. 模型只能从交集内选择 Tool；Tool 执行前再次由代码检查权限、审批和状态。
6. 每次运行记录 Skill 版本、Tool 版本、模型配置、输入输出摘要、审批与最终医院人员决定。

“模型选择了 Tool”不是授权。权限检查必须发生在执行路径中，并且不能只写在 system prompt。

### 4. 最高优先安全策略

安全策略可以由医院管理员按本院流程调整，但建议实现为受保护的 `SafetyPolicy` 资源，而不是可自由删除的普通 Skill：

- 固定保留一个平台级策略 ID；禁止删除、停用和被同名 Skill 覆盖。
- 修改创建新版本，需要至少医疗负责人和系统管理员双重审批。
- 发布前运行固定评测集，展示与上一版本的风险分类差异。
- 模型输出结构化 `RiskAssessment`，仅作为辅助意见；`urgent`、`emergency`、`uncertain` 均进入医院人员队列。
- 医院人员是最终决定者；未决状态下不得自动预约、取消、通知外部人员或执行其他高影响动作。
- 模型超时、格式失败或不可用时进入“无法评估/人工处理”，不得视为低风险。

这满足“用 LLM 做语义判断而非关键词匹配”，同时把不可绕过的流程约束留在代码和审批系统中。

## 建议的阶段边界

第一阶段应实现：Skill/SkillVersion/ToolDefinition/SkillToolBinding 的数据库模型，管理 API，标准 Skill 导入导出，草稿/审核/发布状态，运行时元数据发现与版本固定，预注册 Tool Registry，以及审计。管理界面可以随后建立在稳定 API 上。

第一阶段明确不实现：上传任意代码、开放任意 HTTP URL、消费者自由安装公开 Skill、生产自动回退 fake adapter、由模型自行批准 Tool、由模型自动做最终医疗风险决定。

## 研究局限

这些框架的 Skills 能力在 2026 年仍有部分处于 beta/experimental，尤其 OpenAI Sandbox Agents、Claude Managed Agents 与 Google ADK Skills；具体 API 会变。这里采用的是稳定的架构共识，不建议把 MediPet 的领域模型直接绑定到任一厂商的托管 Skill ID 或运行时对象。LangChain 和 AutoGen 没有与 Agent Skills 完全同构的一等 Skill Registry，因此相关结论主要用于验证 Tool 注册、动态过滤和审批边界，而不是声称所有框架使用同一术语。
