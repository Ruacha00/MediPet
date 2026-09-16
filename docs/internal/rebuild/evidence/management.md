# U05 管理页浏览器验收

日期：2026-09-16。工作树为 `MediPet-Rebuild`，页面 `http://127.0.0.1:5173` 代理当前 API 8010；Chromium headless，桌面 1440×1000。真实模型为 deepseek-flash，真实 Redis 与 Chroma 由当前 API 使用。

## 已通过的真实交互

| 操作 | 结果与证据边界 |
| --- | --- |
| 知识检索 | 一次真实 `/search` 成功，返回条目均有 source、source_id、doc_id、chunk_id，页面来源与响应一致。 |
| 文档导入 | 从页面导入唯一 source_id `u05-browser-b119eff16f2e`，实际写入一个片段；通过 Chroma 按确切 chunk ID 与 metadata 核对后仅删除该片段，复查为空。 |
| Skill 重载 | `general_visit/SKILL.md` 临时加入唯一标记，页面重载后正文长度 287→444；用户问题不含标记，随后一次真实 `/chat` 回答包含 `U05-SKILL-b119eff16f2e`，证明后续请求实际消费更新内容。 |
| 运行详情与来源 | 单条消息及侧栏技术详情均默认折叠；展开后可见本次 `search_knowledge_base` 成功记录及实际来源 `hospital-public / department-ophthalmology:0`，没有从正文生成来源。 |
| 监控 | 页面消费真实 `/monitor` 统计；实例键 `general_0` 等正确显示四角色中文标签。数值为采集时快照，不是性能基线。 |
| 评测协议接线 | 页面真实发出 `/eval/run`；Playwright 只将输入改为三个空案例数组并继续请求，没有替换响应。HTTP 200，真实 EvalReport 的三类 case_counts 均为 0、total=0、baseline_status=candidate、accepted_by=null，页面显示候选报告与无有效评分。 |

真实模型端点请求合计为一次 `/search` 和一次 `/chat`；这个数量不代表内部 SDK 调用次数。空案例评测源码路径不进入任何案例或 Judge 调用，仅验证真实接口与页面接线，**不是正式质量评测**，不能使用它的 0% 作为模型质量指标。正式案例与完整真实报告展示由 V06 独立验收。

## 失败分支与回归

- 32/32 前端测试通过，`npm run build` 通过。Vue 挂载测试覆盖失败 trace、业务确认独立标记、查询空结果/部分失败/HTTP 错误、Skill 解析失败、监控失败及候选/接受基线条件。
- 浏览器受控 HTTP 报告验证 Judge 失败、调用失败、跳过和逐样本详情，不将它算作真实模型结果。390×844、768×1024 评测页没有横向溢出；浏览器 pageerror 为 0。
- 初次脚本错误地预期监控键为 `general`，真实返回为 `general_0`；修正断言后仅继续监控与受控报告检查，没有重复模型请求。该发现同时促成最小 `agentLabel` 修复：仅移除映射查询时的末尾 `_数字`，未知名称仍显示原值。GitNexus 上游风险 LOW，源码补查模板调用，随后再次完成 32 项测试、构建及真实标签检查。

## 清理及证据

临时 Skill 已按原字节还原并再次重载，正文恢复 287 字符；还原前后 SHA-256 为 `76f47702267c33f82591594be9ef2f1851ea82268f41821a0206ef0aeb07c5d3`。清理文档 ID 为 `upload-133346490d553df318dcc1f5df9b2408005567e19ca8cbc330a511e7bf3a0ff6`，只操作其 `:0` 片段，未删除集合或其他资料。聊天事项保留为真实演示历史。

- [管理交互与清理摘要](browser-management.json)
- [真实空案例 API 原始报告](browser-management-eval-empty.json)：run_id `f8c8e13951364f70ad042a423e0fc02c`；候选文件只代表协议验收，不接受为基线。
- 可复现脚本：[check_management.py](../../../../tests/browser/check_management.py)。完整流程会短暂修改 Skill，运行前协调其他模型请求；脚本在 finally 中还原并清理自己的唯一文档。
- 本地截图和完整响应位于被忽略的 `.scratch/browser-management/`：`knowledge-search.png`、`knowledge-import.png`、`skill-and-trace.png`、`monitor.png`、`evaluation.png`、`evaluation-390.png`、`evaluation-768.png`、`evaluation-real-empty.png`。持久 JSON 记录事实，不依赖截图判断是否通过。

完整验收使用 bundled Python 执行 `tests/browser/check_management.py`；仅复现无模型评测接线时增加 `--empty-eval-only`。脚本会在点击按钮前安装输入拦截，确保默认全量案例不会被发送；服务须已启动。
