# 首份真实模型候选：2026-09-16

状态：**first candidate，未接受；不能作为可靠最终基线。** 本轮完成后发现的评测审查缺口和产品协作失败需要分别修正，并用新报告重新验证。本文保留本轮原始结果，不改答案、不删除失败、不覆盖已有候选。

## 执行与证据

- 在真实页面 `http://127.0.0.1:5173` 点击一次“运行评测”，经同源代理请求开发 API 8010。开始 16:19:16，HTTP 响应 16:23:45（UTC+8）；请求耗时 **269.062 秒**，含截图的运行耗时 **269.812 秒**。
- `POST /api/python/eval/run` 无请求 body，使用默认 **54 条意图、12 组多轮、12 组边界**。HTTP 200，页面错误 0，实际请求次数 1，没有重试。
- 模型 `deepseek-flash`，由协调者确认服务已启用 `MEDIPET_THINKING=disabled`。本地非密钥参数和源码表达式见 [model-parameters.json](model-parameters.json)；返回报告记录 Judge `temperature=0.0 / max_tokens=256`。
- 代码基点 `d0d2bdb2a30673035ff1d7dd7db8f0aa9f50a79e`，分支 `refactor/medipet-scenario`；这次执行的是其后工作树实现，以 62 个[运行前](source-manifest-before.json)与[运行后](source-manifest-after.json)文件 SHA-256 定位，前后没有变化。运行脚本快照为 [runner-at-run.py](runner-at-run.py)。
- 输入原文见 [inputs.json](inputs.json)。报告输入 SHA-256：`9ea00961639b03fb3fd0b0a26c91df2537575d404c6bbb7bacc5cf3e3821d378`。
- 运行编号：`0f457b4d311941aab9ad63a16dad216d`。原始响应 [response.json](response.json) SHA-256：`8089f7041ac5d969755c1dc8b80bfb608616b108b6f5af8d0740c8caa0c3eb58`。独立摘要 [summary.json](summary.json)、时序及请求证明 [execution.json](execution.json)。
- 页面证据：[完整报告](report-full.png)、[失败筛选](report-failures.png)、[渲染 HTML](page.html)与[页面文字](page.txt)。页面明确显示候选状态及失败列表。
- 服务保存的候选位于 `data/eval/candidates/0f457b4d311941aab9ad63a16dad216d.json`。接受基线文件运行前后均不存在，没有调用 `accept_baseline`。日志显示 24 个独立场景的 Chroma 记忆集合完成退出清理。

## 原始结果

| 项目 | 本轮记录 |
| --- | --- |
| 意图 | 54/54；Accuracy 1.0000，Macro-F1 1.0000 |
| 有效 Judge 质量项 | 24；其中 15 通过、9 低于 0.75 阈值 |
| 四维平均分 | 相关性 0.9250、准确性 0.6729、完整性 0.6875、有用性 0.6896 |
| 业务结果 | 24 组中 23 通过、1 未通过；受下述评测审查限制 |
| 总结果项 | 49 项中 39 通过，原始 `pass_rate=0.7959`；不是 78 个输入的模型准确率 |
| Judge 失败 / 调用失败 / 跳过 | 原始汇总各 0；调用失败零值不能排除 `judge=False` 路径的漏报 |

这份结果没有同条件前后对照，不支持提升结论。意图集全对只适用于本次固定输入；Judge 低分保留原值，不在人工复核前推定为模型错误或评测错误。

## 完整未通过列表

| 结果 ID | overall | 实际问题或需复核内容 |
| --- | --- | --- |
| `date-followup:turn:0` | 0.450 | 查询今天儿科；已返回真实号源，需结合工具结果复核 Judge |
| `period-followup:turn:0` | 0.450 | 查询明天儿科上午；已返回真实号源，需复核 Judge |
| `list-selection:turn:0` | 0.450 | 查询明天儿科可选号源；需复核 Judge |
| `create-confirm:turn:0` | 0.450 | 查询明天儿科；需复核 Judge |
| `collaboration:turn:0` | 0.600 | 复合号源与“需要的资料”请求只有 Appointment，缺 Guidance 和材料卡 |
| `collaboration:business` | 不适用 | 主辅角色及材料卡两项确定性断言未通过，详见下文 |
| `patient-switch:turn:0` | 0.375 | 查询孩子号源并准备方案；工具已返回 pending，需复核 Judge |
| `history-restore:turn:0` | 0.450 | 查询明天儿科；需复核 Judge |
| `reselection:turn:0` | 0.225 | 查询并准备第一项；实际调用查询和准备，需复核 Judge |
| `archive-restore:turn:0` | 0.625 | `get_wayfinding(origin=大厅,destination=药房)` 返回 `not_found`，该自然语言地点没有解析到预置地点 |

全部 9 个低 Judge 项发生在首轮。Judge 当前只返回四项分数，没有评分理由；评测传入的是本轮开始前的记忆背景，不包含本轮工具事实全文。该输入边界使人工复核尤其必要，不能仅依据低分判定真实工具结果为虚构。

协作场景首轮原文为：“给我查明天儿科门诊号，并说明第一次带孩子来需要的资料。”

- `agents.include=appointment,guidance`：实际 `[appointment]`，失败。
- `has_artifacts=slot_list,visit_checklist`：实际 `[slot_list]`，失败。
- `appointments.count=0`：实际 0，通过。

Appointment 实际调用 `search_slots` 和 `search_knowledge_base`；检索返回 5 条带来源片段，回答中也写了材料说明，但没有 Guidance 角色与 `visit_checklist` 卡片。因此是可复现的业务协作缺口，不能用有材料文字来替代要求，也不是下面来源计数审查问题造成的这项失败。

`archive-restore` 的归档恢复业务断言通过，但首轮地点查询失败仍保留在 trace 与质量项中。预置“无号源”和注入“检索失败”也实际返回错误状态，它们是相应边界场景的预期行为，不等同于 Judge 或模型调用异常。

## 本轮可信度限制与后续交接

1. 运行期间的独立审查发现：`EndToEndEvaluator._turn` 在 `judge=False` 时先返回，未执行后面的失败 trace 检查，边界轮的模型/整合调用失败可能漏入汇总。本轮保持冻结，没有边跑边修改。
2. `_business_facts` 用 `result_summary.result_count` 代替实际来源数量，来源断言可能在没有可追溯来源时假通过。本轮成功检索的 trace 确有来源，但不据此免除机制修正及反例回归。
3. 审查进一步确认 Judge 没有接收本轮实际卡片、工具事实与业务时钟，无法据这些事实核对首轮回答。需要补充真实背景输入，但不改评分算法，也不追改本次原始分数。
4. 实际同步请求 269.062 秒超过当时 Nginx 180 秒等待。开发代理完成不能代替容器代理可用；需要为评测入口修正等待策略并通过真实容器入口重新取得报告。

协调者分别负责或派发上述有限修正；完整原始 JSON 和截图保持不变。修正后记录新代码指纹、用新输出目录再评测，须另行获得启动通知。本次没有自动接受、改参数或重试。
