# 06: 导入、导出并隔离 Skill 包

Type: implementation
Status: resolved
Blocked by: 05

**What to build:** 让系统管理员安全地导入和导出 Agent Skills-compatible 指令包，持久化非执行资源，并将包含脚本或其他可执行内容的包隔离而不是运行。

- [x] 合法的 SKILL.md、references、schemas 和 templates 可以导入为新草稿版本。
- [x] 导出的包可以再次导入并保留等价的指令、资源、Schema 和治理元数据。
- [x] 文本资源存入 PostgreSQL，归属于明确且不可变的 Skill 版本。
- [x] 包结构、manifest、文件类型、单文件大小、总大小和资源路径均在保存或发布前校验。
- [x] 包含脚本、二进制或其他可执行内容的导入进入 quarantined 状态。
- [x] quarantined 内容可供管理员检查，但不能审核通过、发布、绑定执行能力或被 Runtime 加载。
- [x] 导入内容不能覆盖平台命名空间、SafetyPolicy 或已发布不可变版本。
- [x] Prompt 注入和越权静态检查产生可理解的发布阻止原因。
- [x] 导入、隔离、导出和拒绝操作均产生不包含敏感内容的审计记录。

## Answer

已实现受管理认证保护的 ZIP 导入与导出 API、Agent Skills manifest 和资源校验、
PostgreSQL 版本化资源持久化，以及脚本、二进制和可执行内容的隔离流程。隔离版本
可检查和导出，但不能编辑、审核、发布或进入 Runtime；静态越权检查、命名空间与
不可变版本保护会返回明确原因，相关导入、隔离、导出和拒绝操作均写入无内容审计。
