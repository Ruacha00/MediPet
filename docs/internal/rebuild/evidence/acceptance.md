# V09 验收证据覆盖（待人工接受）

日期：2026-09-16。范围：当前 `MediPet-Rebuild` 工作树的总计划 §17、R01—R12、全部 40 个 issue 及已有证据。本文只归纳实际记录，不补跑模型、测试或索引，不改变 issue、spec 和总计划状态。

**运行验证和最终技术检查已完成，交付尚未全部关闭。** 当前为 37 个 `done`，V06、V08 为 `in_progress`，V09 为 `todo`。第二份完整容器报告已保存，用户尚未答复人工接受；V08材料及V09核对已就绪，待V06前置关闭。以下记录实际证据，不把 candidate 或问题已送达当作用户批准。

依据：[总计划](../../../../IMPLEMENTATION_PLAN.md)、[需求映射](../README.md)、[S08](../specs/S08-delivery/spec.md)、[V09](../specs/S08-delivery/issues/V09.md)。issue 文件头是当前状态依据；最终完成时仍需同步执行检查点和各级验收表。

## 证据层次与有效范围

| 层次 | 已有证据 | 可以证明什么；不能外推什么 |
| --- | --- | --- |
| 源码、契约及固定数据 | B01—B04、H01—H04、K01；[内部契约](../contracts.md)、[后端导入记录](../source-map.md)、[前端导入记录](../frontend-source-map.md) | 固定来源、统一数据、入口与原机制保留。导入指纹或代码阅读不等于运行成功。 |
| 确定性 API 闭环 | [I04](../specs/S06-api/issues/I04.md)，24 passed，无跳过 | 实际 API、Agent 工具、医院服务和记忆逻辑执行；模型、Redis、Chroma 为替身，固定时钟。窗口删除模拟不等于真实 TTL。 |
| 评测器正确性 | [V02](../specs/S08-delivery/issues/V02.md)，最终评测器/API 34 项通过 | 包括不评分边界轮的失败记录、实际来源计数、Judge 本轮事实/身份/时钟输入及不泄漏预期断言/未来动作。假分类器与 Judge 分数不代表模型质量。 |
| 最终后端回归 | [final-tests.xml](final-tests.xml)：389 passed、21 skipped，0 failures、0 errors | XML 共 410 项；21 项因未显式开启真实组件跳过。真实存储另行认定，不能把跳过当通过。 |
| 真实存储 | [V03 / storage.md](storage.md) | 19 passed 中只有 18 项使用真实存储；另 1 项是名称筛选命中的替身，1 项真实归档竞态按设计跳过。Redis 7.4.11、客户端 5.2.1；Chroma 服务/客户端 0.5.23。摘要/画像模型仍为替身。 |
| 实际业务和管理浏览器 | [V04](../specs/S08-delivery/issues/V04.md)、[browser.md](browser.md)、[management.md](management.md) | 真实 deepseek-flash、Redis、Chroma；业务六组、公开信息/导诊/急症三组断言通过，管理操作及窄屏检查通过。它们不是全量模型评测。 |
| 前端逻辑与构建 | [U05](../specs/S07-ui/issues/U05.md)，32/32 测试、生产构建通过 | 真实 Vue 挂载配受控 HTTP，覆盖失败、迟到响应和展示契约；真实接口与页面证据由上一行独立提供。 |
| 容器与重置 | [V05 / runtime.md](runtime.md)，4 项重置替身测试及五阶段真实容器探针 | 隔离项目四容器重建、卷持久化、同源代理、初始化幂等与精确定向重置通过；没有真实模型请求。Chroma 持久化标记用固定向量，不代表模型记忆质量。 |
| 短 JSON 配置探针 | [deepseek-thinking.md](deepseek-thinking.md) 及[原始摘要](deepseek-thinking-probe.json) | 两次相同 prompt 对照验证 disabled 配置与短预算输出；不表示准确率或整体提升。关联 223 项回归仍是替身层。 |
| 正式真实模型 | [第二份容器候选](../../../../evaluation/reports/live-20260916-container/notes.md)：54/54 意图、24/24 质量项、24/24 业务组 | 真实容器页面单次请求 HTTP 200，170.344 秒，0 pageerror；模型调用/Judge 异常/跳过均 0。仍为 candidate，保留“大厅”简称查询失败，人工接受待答复。 |

以上测试集合相互重叠，不相加为模型质量率；最终后端数为 389/21，早期 356/21 是前一版本快照。重置 4 项已包含在最终后端套件，前端 32 项另列。V03 的 18 项真实组件及另一次隔离评测工厂真实存储/假 SDK 检查独立记录；受控失败报告与[真实零样本报告](browser-management-eval-empty.json)只证明 UI/协议。

第二份运行编号为 `a13acc91e787406bb909067a456be4a3`，62 个运行源文件前后指纹相同。[容器运行前](../../../../evaluation/reports/live-20260916-container/container-before.json)与[运行后](../../../../evaluation/reports/live-20260916-container/container-after.json)均为 `baseline_sha256=null`；服务候选与 HTTP 报告规范化 JSON hash 一致，`accepted_by=null`。49/49 是 1 个意图汇总、24 个质量项、24 个业务组的结果项，不是 78 个输入的通用准确率。[首份候选](../../../../evaluation/reports/live-20260916-full/notes.md)及失败保留原样；两次间代码、Judge 背景和环境有变化，不声称同条件提升。

## R01—R12 入口与证据

| 需求 | 实际入口与实现 | 已有验收 | 当前边界 |
| --- | --- | --- | --- |
| R01 医院、科室、医生 | 页面聊天 → `POST /chat` → `query_hospital_catalog`；[医院服务](../../../../hospital/service.py)、[统一数据](../../../../hospital/demo_data.json) | H02/H03/A01—A03；[公开信息浏览器原始响应](browser-public.json)；第二份固定意图集 54/54 | 仅针对预置医院与固定输入，不承诺任意问法全对。 |
| R02 号源查询 | 聊天 `search_slots` → 真实服务列表 → [业务卡片组件](../../../../frontend/src/components/BusinessArtifacts.vue) | H04、I04；V03 重初始化保留库存；[业务浏览器原始响应](browser-business.json)；第二份日期/时段承接断言通过 | 余量为查询快照，确认时重新校验。 |
| R03 创建预约 | 号源选择 → `prepare_appointment` → pending；按钮 → `POST /appointment-proposals/{proposal_id}/confirm` → `confirm_proposal` | I03/I04、V03/V04；第二份确认前 0、后 1 条预约，库存 -1，重复确认/末号竞争通过 | pending 不执行；服务事务与模型回答分别验收。 |
| R04 查询及取消 | 聊天 `list_appointments`、`prepare_cancellation`；同一薄确认接口执行取消 | I04、V03/V04；第二份 active→cancelled、库存 +1，重复取消同回执 | 原创建回执保留当时快照，不改写为当前状态。 |
| R05 准备与流程 | `get_visit_checklist` 与 `search_knowledge_base`；[17 份知识](../../../../knowledge/)、[四组 Skills](../../../../skills/) | H03、K01—K03、I04；V04 卡片/来源；第二份材料与协作场景通过 | 来源是实际工具返回，不由回答文字推定。 |
| R06 文字指引 | `get_wayfinding` 返回预置普通/无障碍路线，页面展示路线卡 | H03 固定/未知路线；V04 门诊大厅→药房无障碍卡；第二份 accessible-route 断言通过 | **“大厅”简称仍返回 `not_found` 并澄清**；归档恢复和 Judge 通过不表示该查询成功。不提供动态导航。 |
| R07 多轮与患者区分 | 页面选择患者/事项；`_load_visit` 服务器绑定，`VisitIdentity` 贯穿工具与[记忆](../../../../memory/conversation_memory.py) | I04、V03/V04；第二份儿童有预约/选择/pending 前置后切本人，预约与选择均为 0 | 绑定和非空前置已验证；实际模型报告仍待人工接受。 |
| R08 事项与完整历史 | `/patients`、`/visits`、`PATCH /visits/{conv_id}`、`/visits/{conv_id}/messages`；[VisitStore](../../../../memory/visit_store.py)、[患者事项组件](../../../../frontend/src/components/PatientVisitPanel.vue) | I01/I04、V03/V04/V05；第二份窗口过期、归档恢复、改选旧方案断言通过 | 完整消息与有限模型窗口分开保存。 |
| R09 多 Agent 协作 | [编排器](../../../../agents/agent_orchestrator.py) 主辅路由、`run_parallel`、整合器；聊天响应角色、卡片、trace | A02/A04、I04、V04；第二份原失败措辞同轮产生 appointment + guidance、slot_list + visit_checklist，预约仍为 0 | 首份协作失败保留；不以跨轮角色或只有材料文字代替通过。 |
| R10 导诊与急症 | [共享检测](../../../../core/emergency.py) 位于 API 普通记忆/模型前；固定响应、contact_only 卡片 | A05/I04/V04；第二份模型不可用夹具中仍中断，`model.calls=0`、`memory.recall_calls=0`；反例通过 | 只覆盖预设演示信号；contact_only 不表示已通知人员。 |
| R11 知识/Skill 管理 | `/search`、`/knowledge/add`、`/knowledge/upload`、`/knowledge/stats`、`/skills`、`/skills/reload`；[RAG 管理器](../../../../mcp/tool_manager.py)、[Skill 加载器](../../../../core/skill_loader.py) | K02/K03 33 项替身；V03 真 Chroma；U05 实际导入来源、重载后真实模型消费标记，精确清理并恢复 Skill | 既定功能已有分层证据；最终材料不得将重载解释为在线学习模型参数。 |
| R12 监控/评测 | `/monitor`、`/trace/tool/{request_id}`、`/metrics`、`/eval/run`；[评测器](../../../../evaluation/evaluator.py)、[隔离运行工厂](../../../../api/evaluation_runtime.py) | V02 34 项；U05 真实监控；第二份完整容器报告及页面展示，实际 Judge 背景和来源保留 | 用户尚未接受基线；结果项全通过不等于所有工具查询成功。 |

业务/API 入口统一在 [api/main.py](../../../../api/main.py)，页面壳为 [App.vue](../../../../frontend/src/App.vue)，客户端为 [backends.js](../../../../frontend/src/lib/backends.js)。以上映射延续工作索引中的实现/验收 issue，不以一个类别的证据替代另一个类别。

## 总计划 §17 逐项对照

| 编号 | 完成定义 | 证据及当前判断 |
| --- | --- | --- |
| P1 | R01—R12 可运行入口与证据 | 上表覆盖全部入口、分层证据及第二份模型结果；依赖状态仍待人工接受及最终收束，未自动关闭 issue。 |
| P2 | 医院、医生、号源、费用、地点一致 | H02/H03、K01、V01 统一事实核对；V04 卡片/来源实测；第二份真实号源、方案和材料响应已保存。地点简称未匹配的限制已单列。 |
| P3 | 页面查询→选择→确认→查询→取消 | V04 六组真实业务断言及原始响应通过。 |
| P4 | 未确认不预约、重复确认同一执行 | I04 文字确认不执行；V03 WATCH 并发；V04 pending 无执行及双击单回执。 |
| P5 | 本人/家属上下文、事项、预约隔离 | I04 非空前置、V03 患者过滤、V04 切换刷新；第二份实际患者切换断言通过。 |
| P6 | 刷新/归档恢复，压缩不删完整历史 | I04、V03 实际 TTL 与完整消息、V04 历史相等、V05 重建持久化。 |
| P7 | 复合需求展示真实主辅与结果 | I04、V04 及第二份原失败措辞均有同轮双角色、两类卡片与工具 trace。 |
| T1 | 九类机制可对应源码讲解 | [V07](../specs/S08-delivery/issues/V07.md) 六文档逐入口说明三路权重、主辅并行、三次工具循环、MCP 仅 RAG、患者作用域、事务和薄确认。 |
| T2 | 成功/失败 trace 可核对、卡片来自数据 | A03/A04、K02、I02/I04、U05；第二份保留成功来源、注入检索失败、无号源和大厅 not_found。业务回执独立；24 个质量项保留实际 judge_context。 |
| T3 | 急症不依赖普通模型、反例不误执行 | A05/I04/V04；第二份故障夹具验证零模型/零记忆召回及反例；确认工具不向模型开放。 |
| T4 | 业务测试、真实存储、浏览器关键路径通过 | 最终后端 389 passed / 21 skipped、V03、V04；真实归档竞态跳过保留，不算通过。 |
| T5 | 前端构建与 Compose 启动 | U05 32 项及构建；V05 重建/持久化/代理；第二份实际 Nginx→API 完整评测 HTTP 200。170.344 秒未跨旧 180 秒边界，不能声称实测了超 180 秒等待。 |
| T6 | 实际模型完整保存、列出失败、无伪造提升 | 两份原始报告、输入/代码指纹、页面、失败和限制均保存；第二份 54/54 意图、24/24 质量、24/24 业务。**人工接受未答复，V06 未关闭。** |
| T7 | 对外品牌及旧场景扫描 | 运行代码/注释/配置/前端/Skills/知识/评测与最终对外文档扫描无实际旧功能残留；退款禁止语及旧标签负例保留。102份Markdown共582处本地链接有效；S04过时阶段文字已修正。内部计划/源码对照按规则允许来源名。 |
| G1 | 使用现有仓库，未初始化第二仓库 | 本次 `git rev-parse --git-common-dir` 指向原 MediPet `.git`；当前目录 `.git` 是文件。 |
| G2 | worktree 关联有效，不覆盖原修改/备份 | Git dir 为原仓库 `.git/worktrees/MediPet-Rebuild`；B01/B02 已记录来源读取和原状态保持。本次只读当前树，不写原工作区、stash、外部归档，未重做全目录差异调查。 |
| G3 | 一套启动路径和架构 | [Compose](../../../../docker-compose.yml)、[架构文档](../../../architecture.md)、V05；EXECUTION 记录主项目 8088 实际首页/health 200，旧开发服务已停。V08 材料就绪，依赖 V06 尚未关闭。 |
| G4 | 外部源路径清单、个人运行数据不提交 | 项目外定位清单未导入，内部导入记录不是外部清单。秘密值/忽略路径扫描无命中，索引运行源hash无变化，见[扫描记录](delivery-scan-final.json)。报告只保留演示证据。 |
| G5 | 当前树影响与提交前完整检查 | [最终检查](change-analysis-final.md)与[完整JSON](change-analysis-final.json)：9批覆盖212改动路径、2372不同符号、220条索引流程，无部分/截断结果；实际暂存区未变。静态图边界由源码与运行证据补核。 |

## 仓库与分析边界

此前只读 Git 核对及当前 [EXECUTION](../EXECUTION.md)一致：分支 `refactor/medipet-scenario`，HEAD 和本地 `origin/refactor/medipet-scenario` 均为准备提交 `d0d2bdb2a30673035ff1d7dd7db8f0aa9f50a79e`。准备阶段已推送，实现仍未提交/未推送；`.git` 仍关联原仓库 worktree。本文更新没有执行 Git 写入或操作原工作区、stash、备份。

此前检查 `.env`、虚拟环境、前端依赖、`.scratch`、`data`、`.gitnexus` 均命中忽略规则，候选路径没有运行目录。主任务内容级扫描与路径检查分别记录，最终范围见扫描JSON；该记录不输出任何实际秘密值。

总计划 §2.2 指定的项目外源码定位/指纹清单不在版本控制候选内。`source-map.md` / `frontend-source-map.md` 是允许保留的内部导入对照；正式评测的 `source-manifest-before.json` 是本项目相对代码路径和 hash，用来固定本次运行版本，不是外部源码定位清单。浏览器 JSON 包含自行生成的演示事项、预约和回执证据；它们不能混入个人配置、真实就诊资料或凭据。

首轮8批182文件的原记录保留；最终9批结果使用最新2,981 nodes / 7,084 edges / 220 flows索引，FTS成功。流程抽取仍有入口/callee未展开和预算边界，已在最终检查中披露并以源码、389项后端回归及分层运行证据补核，不以图代替实际执行。分析之后只新增检查产物及回填本表等状态文档，运行源码未变。

## 最短待办

1. **V06**：等待用户明确接受或保留 candidate 的答复。未答复前不调用 `accept_baseline`、不填写复核人；接受后保存新 accepted 副本，保留两份原始候选。答复保留候选也不等于完成基线接受。
2. **V08**：材料及对外命名检查已就绪，待V06实际决定同步基线状态并关闭依赖。
3. **V09 / 主任务**：技术核对及本记录已完成，待V06→V08前置关闭后登记最终状态。未完成项不打勾，不以报告全通过代替人工接受。

当前未发现需求入口遗漏；“大厅”简称和真实归档竞态未验证等边界保留。所有依赖关闭前保持重构未全部完成。
