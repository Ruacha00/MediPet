# 容器真实模型候选：2026-09-16

状态：**candidate，未接受，等待用户人工复核。** 本次从真实容器页面运行完整默认数据。预置结果项全部通过，但仍保留一条自然语言地点查询失败，不能把报告理解为所有工具调用都成功或项目没有限制。

## 运行与配置

- 页面 `http://127.0.0.1:18088`，Nginx 同源代理至验证容器 API（宿主入口 `http://127.0.0.1:18000`）。点击时间 2026-09-16 16:34:48，完成 16:37:39（UTC+8）。
- 实际仅一次无 body 的 `POST /api/python/eval/run`，HTTP 200，响应耗时 **170.344 秒**，含展示截图总耗时 **170.844 秒**；页面错误 0，没有重试。
- 模型 `deepseek-flash`，Anthropic 兼容地址 `https://api.deepseek.com/anthropic`，`MEDIPET_THINKING=disabled`。容器实值及关键代码 SHA 见 [container-before.json](container-before.json)，不含密钥。本地参数白名单、角色默认预算与各调用表达式见 [model-parameters.json](model-parameters.json)。
- Judge 沿用四维评分、`max_tokens=256`、`temperature=0.0`；本版本将本轮实际卡片、工具轨迹、身份和业务时钟一并提供为背景。24 个质量结果均保留实际 `judge_context`。
- 意图向量为本地字符 n-gram，知识向量为 Chroma `all-MiniLM-L6-v2`。场景使用独立真实 Redis 前缀与 Chroma 记忆集合；业务时钟来自用例，模型实际处理固定场景。

## 原始结果

| 项目 | 结果 |
| --- | --- |
| 输入 | 54 条意图、12 组多轮、12 组边界 |
| 意图 | 54/54；Accuracy 1.0000、Macro-F1 1.0000 |
| 质量结果 | 24 个有效 Judge 项，24 个达到 0.75 阈值 |
| 四维平均分 | 相关性 0.9917、准确性 1.0000、完整性 0.9583、有用性 0.9604 |
| 业务结果 | 24/24 组确定性断言满足 |
| Judge 失败 / 调用失败 / 跳过 | 0 / 0 / 0 |
| 结果项 | 49/49；由 1 个意图汇总、24 个质量项和 24 个业务组组成 |

结果项通过率不是 78 个输入的模型准确率；四维分数是本次 Judge 评价，不代表未来任意问题的真实准确率。没有自动接受、人工删改失败或改答案。

## 已核对的业务事实与保留问题

- 协作原失败措辞现在在同轮产生 `appointment + guidance`，卡片为 `slot_list + visit_checklist`，尚未确认时预约数仍为 0。
- 创建场景确认前 0 条、确认后 1 条预约且库存变化 -1；取消场景从 active 变 cancelled、库存 +1。重复创建/取消返回相同回执，末号竞争只执行一次。
- 患者切换场景先确有孩子的号源选择与 pending 方案，再核对本人预约和选择均为 0；完整历史过期恢复、归档恢复和改选旧方案断言满足。
- 预设急症在模型不可用夹具中仍完成固定中断，实际 `model.calls=0`、`memory.recall_calls=0`。否定与一般急症咨询未触发中断。
- 成功知识查询的来源按实际 `trace.sources` 计数；注入检索失败时来源为 0 并保留 `retrieval_failed`。无号源返回 `no_slots`，是对应边界的预期行为。

**保留的真实业务查询失败**：`archive-restore:turn:0` 中“提供大厅到药房的文字说明。”仍调用 `get_wayfinding(origin=大厅, destination=药房)` 并得到 `not_found`。模型如实提示地点未找到；归档恢复断言和本轮 Judge 均通过，但这不证明“大厅”简称已经解析成功。原始 trace 完整保留，本次没有扩大地点模糊匹配。该工具正常返回业务错误（`call_success=true`），因此不计为模型/工具调用异常。

## 报告与可复核文件

- 运行编号：`a13acc91e787406bb909067a456be4a3`，服务候选路径 `/app/data/eval/candidates/a13acc91e787406bb909067a456be4a3.json`。
- [原始 HTTP JSON](response.json) SHA-256：`3a3bd50e41a6ff8543ebada02737302c2b8f8ee849906cd656cf988732e1dfa2`；[摘要](summary.json)、[时序与请求记录](execution.json)。
- [输入原文](inputs.json)与 API 输入 SHA-256：`9ea00961639b03fb3fd0b0a26c91df2537575d404c6bbb7bacc5cf3e3821d378`。
- 工作树基点 `d0d2bdb2a30673035ff1d7dd7db8f0aa9f50a79e`，分支 `refactor/medipet-scenario`；以[运行前](source-manifest-before.json)/[运行后](source-manifest-after.json) 62 个文件 SHA 定位未提交实现，期间无变化；[脚本原件](runner-at-run.py)。容器中 agents/evaluator 指纹已由协调者与这份工作树核对一致。
- [完整页面](report-full.png)、[失败筛选页面](report-failures.png)、[HTML](page.html)、[页面文字](page.txt)。显示候选状态；失败筛选为空，没有将报告改成已接受。
- 宿主基线文件前后均不存在；容器 `api-data` 的[运行前](container-before.json)与[运行后](container-after.json)核对同样为 `baseline_sha256=null`。容器候选与 HTTP 响应规范化 JSON 的 SHA-256 均为 `7712e4f5b85e2c9b0472234f52bb4ea9c979359586df9fa1274aeee66600b07d`，运行编号、candidate 状态及 `accepted_by=null` 一致。容器核对由协调者实际执行，与宿主文件检查分开保存。

## 与首份候选的关系

[首份候选](../live-20260916-full/notes.md)原样保留。两次输入 SHA 相同，但中间修改了 `agents/agent_orchestrator.py`、`evaluation/evaluator.py`、`frontend/nginx.conf` 及浏览器脚本；部署环境与 Judge 背景也不同。因此不声称严格同条件质量或性能提升。

新容器使用评测专属 1800 秒代理等待，本次 170.344 秒请求完整返回。它验证了本次容器实际链路，**本次并未跨过旧 180 秒边界**；此前开发请求 269.062 秒是调整等待配置的依据，不能把两次条件混成一次超时验收。

人工复核需要查看实际回答、卡片、工具状态和 Judge 背景，特别是上面的地点简称限制，然后明确决定是否接受这份候选。当前 `accepted_by=null`，没有生成已接受基线。
