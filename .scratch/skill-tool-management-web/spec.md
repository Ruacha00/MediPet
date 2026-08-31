# Skill / Tool 能力治理台

Type: spec
Status: ready-for-agent

## Problem Statement

MediPet 已有非生产环境的 Skill 与 Tool 管理 API，但没有可视化入口。维护者目前只能手工构造 Bearer Token 请求，难以完整核对 Skill 版本、发布阻断项、资源、Tool 契约、绑定关系及治理审计。

首版需要提供一个仅面向开发环境维护者的桌面 Web 治理台，覆盖现有 Skill 生命周期和 Tool 治理能力，并补齐审核闭环所必需的绑定查询、草稿解绑、文本资源查看及审核后绑定冻结。它不是生产医院管理后台，也不建立管理员账号或角色系统。

## Outcome

唯一维护者可以从 `/admin/capabilities` 进入独立治理台，完成以下闭环：

1. 创建或导入 Skill 草稿；
2. 编写指令、检查资源和 Tool 绑定；
3. 比较版本并提交审核；
4. 检查发布阻断项后发布、退休或激活旧版本；
5. 查看受信任 Tool 契约并治理启用与审批状态；
6. 在统一时间线中追溯 Skill 与 Tool 的治理变更，按需包含运行事件。

所有治理事实以 FastAPI Registry 的响应为准。Web 不复制生命周期规则，也不把 Tool 实现、任意 URL 或可执行代码变成可编辑内容。

## Canonical Language

使用根目录 `CONTEXT.md` 中已经确定的名称：

- `Skill` 是版本化指令与资源集合，本身不授予执行权限；
- `Tool` 是部署提供的受信任查询或操作能力；
- `Tool 绑定` 是特定 Skill 版本与 Tool 版本之间的使用关系；
- `能力治理` 是对版本生命周期、可用状态及绑定关系作出的受审计决定。

界面不使用“技能”“插件”“提示词管理”或“任意接口”等近义表达。Skill 使用“提交审核、发布、退休、激活”；Tool 使用“启用、停用、要求审批”。

## Actor and Permission Boundary

### First release actor

首版只有一个 `development-admin`。任何能够访问非生产 Web 的人都被视为该开发管理员，并拥有全部查看与治理能力。

该选择只适用于当前单人、受信任开发环境：

- 管理页面和 Web 代理只在 `MEDIPET_ENVIRONMENT != production` 时存在；
- production 对管理页面及代理返回 `404`；
- 浏览器不接收、存储或提交 `MEDIPET_MANAGEMENT_TOKEN`；
- Next.js 服务端从私有运行环境读取 Token，并在调用 FastAPI 管理 API 时注入；
- FastAPI 保留现有 Bearer Token 校验，Web 代理不是绕过后端认证的第二入口；
- 当前审计 actor 继续记录为 `development-admin`。

开发 Web 不得暴露到不受信任网络。首版不以 UI 警告替代该部署边界。

### Future access-gate seam

所有管理页面数据和变更请求必须集中经过一个服务端授权接缝，例如：

```text
authorizeCapabilityAdmin(request, action) -> AdminPrincipal
```

首版实现返回固定的开发管理员并放行。页面组件不得自行读取环境变量或散落角色判断；代理处理器必须在读取管理 Token 和调用 FastAPI 之前经过该接缝。未来可以在这一处接入 session、身份提供方和动作权限，但本规格不定义登录页、角色矩阵或 RBAC 数据模型。

### Boundaries that UI cannot override

- Tool 只能来自已部署、受信任的 Tool Provider；治理台不能创建实现、上传代码或登记任意执行 URL。
- 写 Tool 的审批要求不能关闭；缺少实现的 Tool 不能启用。
- Runtime 仍独立检查 Skill 发布与绑定、Tool 启用与可用性、就诊阶段和代码级授权。
- `published` 与 `retired` Skill 版本不可修改。
- 提交审核后，Skill 指令、资源和 Tool 绑定全部冻结。
- 隔离 Skill 不能提交审核、发布、绑定 Tool 或被 Runtime 加载。
- production 不注册开发管理 API；Web 不能把这一限制降级为前端隐藏。

## Information Architecture

治理台使用独立后台壳，不复用患者 ChatShell 或就诊事项侧栏。

| Route | Purpose |
| --- | --- |
| `/admin/capabilities` | 服务端重定向至 Skills 列表，不建设总览仪表盘 |
| `/admin/capabilities/skills` | Skill 列表、搜索、筛选、新建和 ZIP 导入入口 |
| `/admin/capabilities/skills/new` | 创建 instruction-only 或 tool-assisted Skill |
| `/admin/capabilities/skills/{skillId}` | Skill 详情、版本选择、比较、资源、绑定和生命周期操作 |
| `/admin/capabilities/tools` | Tool 列表、搜索和状态筛选 |
| `/admin/capabilities/tools/{toolId}` | Tool 契约、版本、Schema、启用和审批治理 |
| `/admin/capabilities/audits` | 合并的 Skill / Tool 审计时间线 |

患者对话界面不显示治理台入口。治理台侧栏提供“返回对话”链接。

## Visual Direction

界面采用“临床能力登记簿”方向：延续现有安静的临床编辑感，同时让治理状态像一份可核查的版本台账，而不是通用 SaaS 仪表盘。

- 沿用现有 `paper`、`ink`、`teal`、`gold`、`urgent` 色彩变量；红色只用于阻断、失败和不可逆影响。
- 左侧使用深青色窄导航；主工作面为纸张色，内容以清晰分隔线和紧凑表格组织。
- 页面标题继续使用现有中文衬线字体；正文使用现有中文无衬线字体；Skill slug、Tool ID、版本和 Schema 使用等宽字体。
- 版本详情使用纵向“版本脊柱”作为记忆点：版本号、状态、创建时间和活动标记沿一条时间轴排列。
- 避免每个区域都使用悬浮圆角卡片。仅确认框、阻断摘要和关键状态使用抬升层级。
- 动效限于页面首屏轻微进入、状态切换和操作反馈；不对治理表格使用装饰性动画。

后台壳自己管理 `100svh` 和内容滚动，不能受当前全局 `body { overflow: hidden }` 影响。首版只验收桌面端：最低 `1280 × 720`，同时验证 `1440 × 900`；不承诺手机或平板布局。

## Shared Interaction Rules

- 列表首屏由服务端加载，搜索和筛选在浏览器端对已取得的数据即时执行。
- 不做分页；空 Registry 是正常空状态，不显示错误。
- 所有 mutation 使用保守提交：请求期间禁用相关控件，不做乐观更新；成功后重新读取对象和审计数据。
- 重复点击不得发送第二个并发 mutation。
- `401` 在 Web 层显示为“开发管理认证未配置或不一致”，不得要求操作者在浏览器输入 Token。
- `404` 表示对象或版本不存在；production 的管理页面本身保持不可发现的 `404`。
- `409` 展示 Registry 返回的状态冲突或治理规则原因，并保留当前页面内容。
- `415/422` 在导入或表单附近展示安全错误，不显示 Token、堆栈、文件系统路径或原始异常。
- 日期以本地时区展示，同时在 `title` 或详情中保留完整时间。
- 状态不能只靠颜色表达；Badge 同时包含文字与图标或形状差异。
- 成功使用页面内 toast；失败同时提供靠近操作位置的可恢复说明。

以下操作会立即影响运行时，提交前必须显示对象、版本、当前状态、目标状态及影响摘要，并要求二次确认：

- 发布、退休或激活 Skill 版本；
- 启用或停用 Tool 版本；
- 修改 Tool 的审批要求。

创建、编辑、导入、提交审核和草稿 Tool 绑定变更不弹通用确认框，因为它们不改变当前运行时能力。

## Page Specifications

### Admin shell

左侧导航固定包含 Skills、Tools、变更记录和返回对话。顶部显示“开发能力治理”与明显但克制的“非生产环境”标识。主内容区负责滚动；侧栏和页面操作栏保持可见。

授权接缝或管理 API 不可用时，显示完整错误状态，不渲染来自部分请求的可操作控件。

### Skills list

列表行展示：

- display name 与 slug；
- Skill 类型；
- 当前活动版本及状态；
- 最新版本及状态；
- 最新变更时间；
- quarantine reason 或 publish blocker 数量。

支持按名称、slug 和描述搜索，并按生命周期状态、Skill 类型、是否活动、是否存在阻断项筛选。默认排序为“需处理优先”，顺序为 quarantined、draft、in_review、published、retired，再按最近更新时间倒序。

页面主操作为“新建 Skill”和“导入 ZIP”。列表为空时解释 Registry 可以合法为空，并保留这两个入口。

### Create Skill

表单字段严格对应现有 `POST /v1/admin/skills`：

- slug；
- name；
- description；
- skill type；
- instructions Markdown；
- change note。

slug 在输入时提示小写 kebab-case 和受保护命名空间限制，但服务端仍是权威校验者。指令区使用源码编辑与 Markdown 预览双栏；不建设富文本、协同编辑或资源在线创建。

创建成功后进入新 Skill 的版本 1 详情。

### Skill import

导入使用文件选择器，只接受 `.zip`，以 `application/zip` 原样发送现有导入 API。提交前显示文件名和大小，不在浏览器解包或执行内容。

导入成功后进入版本详情：

- 合法包显示为 draft；
- 包含可执行或二进制内容时显示 quarantined；
- quarantine reasons 置于详情顶部，生命周期操作全部隐藏；
- 导出仍可用，便于离线检查。

### Skill detail

页面左侧为版本脊柱，默认选择最新版本；活动版本有独立标记。主区包含：

1. **概览**：名称、slug、描述、类型、状态、活动状态、change note、创建时间、治理元数据和发布阻断项；
2. **指令**：只读 Markdown 源码与渲染预览；
3. **资源**：路径、媒体类型、大小和安全文本预览；
4. **Tool 绑定**：绑定的 Tool ID、版本、effect、可用与启用状态；
5. **比较**：选中版本与直接前一版本的并排比较。

比较至少覆盖名称、描述、类型、指令、资源清单、Tool 绑定和 change note。指令首版可以按整段或行块突出变化，但不引入复杂 diff 编辑依赖；资源比较只比较路径、媒体类型与大小，不比较二进制内容。

“基于最新版本创建草稿”调用现有 PATCH，只允许编辑 instructions 和 change note，并清楚说明名称与描述当前不能在 Web 修改。编辑不会原地改变所查看版本。

生命周期操作按选中版本状态显示：

| Status | Available actions |
| --- | --- |
| `draft` | 编辑新草稿、管理 Tool 绑定、提交审核、导出 |
| `in_review` | 发布、导出；内容、资源和绑定只读 |
| `published` + active | 退休、导出 |
| `retired` | 激活、导出 |
| `quarantined` | 查看元数据与原因、导出 |

UI 可以根据已知状态隐藏无效操作，但服务端必须继续拒绝非法转换。

### Tool binding flow

绑定入口仅出现在 tool-assisted Skill 的 draft 版本。

1. 读取当前绑定和 Tool Registry；
2. 选择一个 available 的具体 Tool 版本；
3. 展示 Tool 名称、effect、允许阶段、启用和审批状态；
4. 提交现有绑定 API；
5. 成功后重新读取绑定和发布阻断项。

解绑只允许 draft。发布版本和 in-review 版本中的绑定全部只读。instruction-only Skill 不显示绑定编辑器，但可以显示空的只读关系说明。

tool-assisted Skill 提交审核前可以保留 disabled Tool 绑定；发布仍由现有服务端规则要求所有绑定 available 且 enabled。详情页应提前展示阻断原因，但不能绕过服务端发布校验。

### Tool list

列表按 Tool ID 分组并展示各版本。默认行显示：

- Tool ID、name 和最新版本；
- read/write effect；
- available、enabled 和 approval required；
- allowed stages；
- 是否存在被绑定的活动 Skill 版本。

支持按 Tool ID、name 和 description 搜索，并按 effect、available、enabled、approval required 筛选。缺少实现的版本置于需处理状态，但不允许启用。

### Tool detail

页面提供版本选择，并展示不可编辑的受信任契约：

- Tool ID、version、name、description；
- input、output 与 confirmation Schema；
- effect、allowed stages；
- provider approval requirement；
- available、enabled 与实际 approval required；
- 当前绑定该版本的 Skill 版本摘要。

Schema 使用格式化 JSON、复制按钮和折叠层级，不提供 Schema 编辑器。

启停或调整审批时，Web 必须以刚重新读取的完整 Tool 状态提交两个字段，避免 PATCH 要求完整 payload 时用旧值覆盖。write Tool 的“要求审批”始终锁定为开启；available=false 时“启用”不可用。确认框要说明当前绑定的活动 Skill 及可能导致的可用性变化。

### Unified audits

Web 读取现有 Skill 与 Tool audit API，映射为统一事件模型并按时间倒序合并。每行显示时间、对象类型、动作、对象 ID、版本和 actor；存在 visit matter 或 turn 标识时只显示去标识化 ID，不显示对话内容。

默认只展示创建、编辑、导入、导出、审核、发布、退休、激活、同步、启停、审批配置、绑定和解绑等治理事件。“包含运行事件”开启后再加入 Skill 选择、Tool 调用和调用拒绝。

支持对象类型、动作和对象 ID 前端筛选，不提供分页或审计删除。

## API Contract

### Existing API reuse

首版继续使用以下现有 FastAPI 端点，不修改它们的主要请求与响应结构：

| Capability | Endpoint |
| --- | --- |
| List Skills | `GET /v1/admin/skills` |
| Create Skill | `POST /v1/admin/skills` |
| Create edited draft | `PATCH /v1/admin/skills/{skill_id}` |
| Import / export | `POST /v1/admin/skills/import`; `GET /v1/admin/skills/{skill_id}/versions/{version}/export` |
| Lifecycle | `POST .../submit-review`; `POST .../publish`; `POST .../retire`; `POST .../activate` |
| Skill audits | `GET /v1/admin/skill-audits` |
| List Tools | `GET /v1/admin/tools` |
| Configure Tool | `PATCH /v1/admin/tools/{tool_id}/versions/{version}` |
| Create binding | `POST /v1/admin/skills/{skill_id}/versions/{skill_version}/tool-bindings` |
| Tool audits | `GET /v1/admin/tool-audits` |

Next.js 为这些操作提供同源、显式的 BFF handlers。不得实现一个能把浏览器提供的任意路径和方法转发到 `/v1/admin/*` 的开放 catch-all 代理。

### Required API additions

#### Read bindings

```http
GET /v1/admin/skills/{skill_id}/versions/{skill_version}/tool-bindings
```

Response `200`:

```json
{
  "bindings": [
    {
      "skill_id": "skill-...",
      "skill_version": 2,
      "tool_id": "hospital.search_slots",
      "tool_version": "1"
    }
  ]
}
```

允许读取任意存在的 Skill 版本；Skill 或版本不存在返回 `404`。

#### Remove a draft binding

```http
DELETE /v1/admin/skills/{skill_id}/versions/{skill_version}/tool-bindings/{tool_id}/versions/{tool_version}
```

成功返回 `200` 及被移除的绑定对象。不存在的 Skill、版本、Tool 或绑定返回 `404`；Skill 版本不是 draft 返回 `409`。操作写入 Tool audit，action 为 `unbind`。

#### Read safe resource content

```http
GET /v1/admin/skills/{skill_id}/versions/{skill_version}/resources/{resource_path:path}
```

对已存储且可预览的 `text/*`、`application/json` 和 `application/schema+json` 资源返回：

```json
{
  "path": "references/process.md",
  "media_type": "text/markdown",
  "size": 1240,
  "content": "..."
}
```

路径必须与数据库中的规范化资源路径精确匹配，不能访问文件系统。不存在返回 `404`；二进制、不可预览或 quarantined 版本返回 `409`，且不返回内容字节。资源查看是只读操作，不增加在线资源编辑 API。

### Required semantic tightening

现有绑定创建端点必须从“draft 或 in_review 可修改”收紧为“仅 draft 可修改”。`submit-review` 成功后，该版本的指令、资源和 Tool 绑定共同冻结。内存与 PostgreSQL Registry 必须遵守相同契约。

## Server-side Web Boundary

浏览器只访问 Next.js 同源管理 handlers。处理顺序固定为：

```text
Browser
  -> explicit Next.js capability-admin handler
  -> authorizeCapabilityAdmin(request, action)
  -> attach server-only MEDIPET_MANAGEMENT_TOKEN
  -> FastAPI /v1/admin endpoint
  -> map safe response or error
  -> Browser
```

Web 容器需要私有的 `MEDIPET_MANAGEMENT_TOKEN` 与明确的 `MEDIPET_ENVIRONMENT`。Token 不得进入 `NEXT_PUBLIC_*`、HTML、React props、客户端 bundle、日志或错误响应。

代理不缓存管理 GET 响应。ZIP 导入和导出保持二进制流，不转为 base64；导出保留安全的 `Content-Disposition` 文件名。

## Acceptance Criteria

### Environment and authorization

- [ ] 非生产环境访问 `/admin/capabilities` 会进入 Skills 列表；production 对所有治理台页面和 Web 管理 handlers 返回 `404`。
- [ ] 浏览器网络请求、HTML、客户端 JavaScript、日志和错误信息中均不出现管理 Token。
- [ ] 所有 BFF handlers 在调用 FastAPI 前经过同一个授权接缝；首版接缝返回固定 `development-admin`。
- [ ] 管理 Token 缺失或与 API 不一致时，页面不可操作并显示安全的配置错误。
- [ ] 患者对话页面没有治理台入口，治理台可以返回对话。

### Skill pages and lifecycle

- [ ] 空 Skill Registry 显示正常空状态，并可新建或导入 Skill。
- [ ] 列表可按文本、状态、类型、活动状态和阻断项即时筛选。
- [ ] 新建表单完整提交现有 Create Skill 契约，成功后进入版本 1 详情。
- [ ] 编辑不会修改原版本，而是通过现有 API 创建新 draft，并要求 change note。
- [ ] ZIP 导入保持原始二进制；合法包进入 draft，可执行或二进制包进入 quarantined。
- [ ] quarantined 版本显示全部原因，不能审核、发布、绑定或预览资源正文，但可以导出。
- [ ] 详情可以切换版本，并清楚区分 latest、active 和 selected version。
- [ ] 审核视图能比较选中版本与上一版本的指令、元数据、资源清单和 Tool 绑定。
- [ ] draft 可以提交审核；in_review 内容和绑定不可修改；发布、退休和激活遵循现有状态机。
- [ ] 发布、退休和激活必须经过包含影响摘要的二次确认。
- [ ] 失败的生命周期请求保持原状态，并展示服务端 `409/422` 原因。

### Resources and bindings

- [ ] 文本、JSON 和 JSON Schema 资源可以在详情页安全预览；接口不能通过路径访问未存储文件。
- [ ] 资源清单展示路径、媒体类型和大小；资源内容不能在线编辑。
- [ ] 任意 Skill 版本的现有 Tool 绑定在刷新后仍可准确读取。
- [ ] 只有 tool-assisted draft 显示绑定编辑器；新增和解绑成功后重新读取 Registry。
- [ ] draft 以外的绑定请求在 UI 不可发起，并由 API 返回 `409` 防止绕过。
- [ ] tool-assisted Skill 的 disabled、missing 或空绑定在发布前可见，并由服务端阻止发布。

### Tool pages

- [ ] 空 Tool Registry 显示正常空状态；治理台不能创建 Tool。
- [ ] 列表可按文本、effect、available、enabled 和 approval required 即时筛选。
- [ ] 详情准确显示 Tool 各版本、Schema、阶段、effect、Provider 审批要求及当前绑定摘要。
- [ ] missing Tool 不能启用；write Tool 不能关闭审批，UI 与 API 均执行限制。
- [ ] Tool PATCH 使用重新读取的完整配置，mutation 期间控件禁用，成功后重新获取状态。
- [ ] Tool 启停和审批变更必须经过影响确认，并展示受影响的活动 Skill 绑定。
- [ ] 治理台不能编辑 Tool Schema、实现、代码或执行地址。

### Audits and failure handling

- [ ] Skill 与 Tool audit 按时间合并；默认仅显示治理事件。
- [ ] 开启“包含运行事件”后显示 Skill 选择、Tool 调用和调用拒绝。
- [ ] 审计可按对象类型、动作和对象 ID 前端筛选，不显示参与者消息或 Prompt。
- [ ] 所有 mutation 防止重复提交，不使用乐观状态。
- [ ] `401/404/409/415/422` 均有稳定、安全且可恢复的界面状态。
- [ ] 刷新页面后，Skill 生命周期、Tool 配置、绑定和审计均从服务端恢复，而不是依赖浏览器临时状态。

### Layout, accessibility, and verification

- [ ] `1280 × 720` 和 `1440 × 900` 下所有页面无意外横向滚动，版本导航、主内容和操作区均可到达。
- [ ] 页面主区在现有 `body { overflow: hidden }` 下仍能独立滚动；长指令、Schema 和审计列表不会截断操作。
- [ ] 键盘可以完成导航、筛选、编辑、确认和关闭对话框；焦点返回触发控件。
- [ ] 表单具有可见 label、字段错误关联和提交中状态；状态与阻断项不只依赖颜色。
- [ ] Web 单元测试覆盖 API 映射、搜索筛选、状态可用操作、比较、确认、重复提交和错误呈现。
- [ ] 后端契约测试覆盖三个新增端点、draft-only 绑定冻结、内存/PostgreSQL 一致性及认证。
- [ ] Docker Compose 浏览器烟测覆盖新建或导入、绑定、审核、发布、Tool 治理、审计和刷新恢复；不调用真实或收费模型。
- [ ] Web lint、TypeScript、Vitest、生产构建及相关 API pytest、Ruff、Pyright 全部通过。

## Out of Scope

- 生产管理页面、医院身份提供方、登录、session、RBAC 或多人审批。
- 手机和平板适配。
- 总览仪表盘、服务端搜索、分页、批量治理或审计导出。
- Skill 删除、版本删除、驳回审核、审核意见、草稿合并或多人协作编辑。
- Skill 名称与描述编辑、资源在线创建或编辑、复杂语义 diff。
- 解除 quarantined 状态或在治理台执行导入包中的脚本。
- Tool 创建、Schema 编辑、实现上传、任意 HTTP/OpenAPI/MCP/脚本连接。
- 修改运行时授权、写 Tool 两阶段确认或医院业务权限。
- 将开发 Web 暴露为受支持的公网服务。

## Existing Decisions Preserved

- ADR-0001 的版本化 Agent Skills 与受信任 Tool Registry 分层保持不变。
- ADR-0004 的“先加载 Skill，再暴露绑定 Tool”运行时顺序保持不变。
- ADR-0009 的外置能力定义与受信任 Python executor 边界保持不变。
- ADR-0010 的“部署同步不授予运行权限”保持不变；新 Tool 版本仍默认停用，新或变更 Skill 仍保持 draft。
- 本规格没有新的难以逆转架构取舍，因此不新增 ADR。
