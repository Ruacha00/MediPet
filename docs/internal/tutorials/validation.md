# 原文基础修订验收

日期：2026-09-16。源码基准：`bb0600c`。交付入口：[MediPet 教程文档中心](../../../文档+简历/文档中心.md)。

本记录对应用户要求“在原有基础上改进”后的最终稿，取代上一轮压缩稿的验收口径。原文结构和内容对应见 [original-based-revision.md](original-based-revision.md)；上一轮不足的分析保留在 [original-vs-current.md](original-vs-current.md)，已标明其历史适用范围。

## 交付范围

- 11 篇 Markdown：学习、定位、使用、流程、架构、重点代码、亮点、面试、指标、未实施扩展构想及中心导航。
- 7 张 SVG：总体架构、三入口流程、角色与 Skills、存储、监控评测、部署、项目总览海报。
- 根 README 新增教程入口；现有架构说明补充混合 PDF 的 OCR 路径；记忆/Skills 说明区分加载配置与 Escalation 固定执行路径。
- 原有 `docs/internal/rebuild/evidence/document-source-manifest.json` 保留原状；本次来源映射与新哈希写入本目录，不覆盖历史清单。

## 内容核对

三个子 agent 分别负责学习/代码、使用/流程、亮点/简历，主 agent 负责图示、导航、构想和集成。每个任务限定文件，不递归派发；完成后冻结，交叉核对仅只读。依据当前代码、Compose、模型契约、用例与已保存运行记录修订，没有运行或改写模型候选。

11篇原教程重新作为编辑底稿，原有98个主章节全部按顺序保留，三处主标题作场景或实现纠正，文档中心追加阅读顺序。保留原设计动机、问答、意图输入例子、模块协作说明、连续代码讲解、存储实验与排错流程；新能力嵌入相关小节。使用指南的旧 Docker Run 章保留开发调试目的，替换为实际支持的 Compose 与本机测试路径。

交叉核对并修正：

1. 确认入口为 `/appointment-proposals/{proposal_id}/confirm`；确认事务位于 `hospital/service.py`，使用 `WATCH/MULTI/EXEC`，没有 Lua。
2. Escalation 固定响应不调用 Skill prompt。`service_boundaries` 配置覆盖六角色，不等于六角色都动态执行该文本。
3. 报告文字、扫描与混合页按真实提取路径说明；原件不保留，派生文字/卡片保存，后续聊天仍可调用模型。
4. 意图字符哈希与 Chroma 客户端语义向量分开；主链路 RAG 按工具调用触发；MCP 为进程内管理器。
5. `.env` 更新需重新创建 API 容器，仅 `restart` 不更新其环境。
6. 完整模型候选 57/58 与后续原场景 3/3 分开；后端 684/21 的时点早于最后指引提示修复，修复后有 119 项定向记录。没有加入未测的效率、成本、并发或医疗准确率。
7. 区分内部 `RoutingDecision.reason` 与 HTTP `routing_reason`；已有画像按字段合并不再列为未实现方向；分诊原文处理不冒充分类器症状实体字段。
8. 区分 Chroma 记录 ID 与 metadata，以及 `/search.results` 的来源字段与聊天工具 trace 的 `sources`；清理操作指南错位子节、重复编号、截断句，并补前置变量定义。

上一轮 GitNexus 用于符号与流程定位；部分 context 返回索引落后和动态调用遗漏，未作为完备调用图或无风险证明，相关结论由当前源码补核。内容修订阶段以原文、当前源码和既有证据直接核对，没有修改函数、类、接口或运行逻辑。用户随后授权提交，提交前按当前工作树执行暂存范围的变更分析。

## 检查结果

| 检查 | 结果与口径 |
| --- | --- |
| 复制与来源 | 原目录 18 文件和只读快照 SHA-256 一致；18 个对应输出均已修订，见 [source-map.json](source-map.json) |
| 原章保留 | 11篇共98个原主章节按顺序对应，逐章名称与行号见 [original-based-structure.json](original-based-structure.json)；教学内容另经人工核对 |
| Markdown | 本地链接存在、围栏成对、JSON 示例可解析；无旧品牌/旧角色残留、替换字符或疑似密钥，详见 [checks.json](checks.json) |
| PowerShell | 教程与 README 共45段示例通过 AST 语法解析；未执行示例，见 [powershell-checks.json](powershell-checks.json) |
| Python 摘录 | 18段 Python 围栏内容通过 AST 语法解析；其中学习/代码文档4段源码摘录由子 agent 与当前源码核对，连续chat摘录另经交叉审阅；语法通过不代表片段可独立运行 |
| API 示例 | 使用指南完整 ChatResponse 示例字段与源码 AST 对齐，命令按当前接口契约核对；示意响应不是实际请求记录 |
| SVG | 7文件 XML 解析通过；与上一轮已用 Chromium 渲染、检查文本边界并人工查看的文件逐字节一致，本轮未重新渲染 |
| 改动范围 | 跟踪文件仅 README 与两篇既有说明；业务源码、测试、依赖、Compose、数据、模型报告未改变 |
| Git | 内容修订时按现有换行配置的 `git diff --check` 通过；提交前另核对完整暂存范围。分支为 `codex/u001-health-consultation`，业务源码基准为 `bb0600c` |

图示渲染检查与截图保存在忽略目录 `.scratch/tutorial-revision-bb0600c/render/`，SVG 本身为可审查交付物。原教程和修改前目标快照位于同一忽略目录下，来源映射保留在仓库文档中。

本轮为文档修订，未重新运行后端/前端业务测试、存储验收或付费模型请求；教程中的历史测试数字均引用已有证据，不作为本轮新增执行结果。HTTP 示例按源码契约核对，PowerShell 仅验证语法。2026-09-16用户授权保存文档提交，具体提交号以 Git 记录为准；本次未推送或重新部署。
