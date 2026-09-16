# U001 完整模型候选

2026-09-16，deepseek-flash / thinking disabled，隔离验证容器18088页面经同源代理至18000 API。单次运行66条意图、15组多轮、12组边界，HTTP200，188.562秒（浏览器流程189.343秒），0页面错误。运行编号 `caeb62fed3f84115889b042b6b822af9`，状态candidate，没有接受基线。

## 实际结果

- 意图66/66，Accuracy与Macro-F1为1.0，仅代表该固定输入集。
- 30/30回答质量结果通过，26/27业务组通过；加意图汇总共57/58结果项，不是93个输入场景的统一准确率。
- 调用异常、Judge失败、跳过均0。新增分诊、药品标签/组合、报告文字两轮场景的角色、卡片与质量均通过。
- 失败为 `accessible-route:business`：首轮正常返回大厅到眼科普通路线；第二轮要求“改用无障碍的文字指引”时，模型虽复述已知起终点却再次请求确认，没有调用工具，最终事实仍为normal。Judge通过不能覆盖该业务失败。

输入和评分未为通过率修改。之后仅补GuidanceAgent同事项已知起终点的只读模式续问规则；原样两轮单独复测另存 [route-fix](../live-20260916-u001-route-fix/notes.md)。该定点验证不改写本报告，也不能称已重跑完整58项或与旧版本做同条件质量提升比较。

## 可核对证据

[原始响应](response.json)、[结果摘要](summary.json)、[执行记录](execution.json)、[实际输入](inputs.json)、[参数](model-parameters.json)。执行前后81个宿主源文件指纹不变；API镜像内65个相关源文件与宿主一致（前端构建与浏览器脚本不在API镜像内）。容器候选规范JSON的SHA-256与HTTP响应一致，接受基线前后均不存在，见 [container-before](container-before.json) 与 [container-after](container-after.json)。

这是固定模型输入的开发验收，不是临床有效性、安全认证或任意报告OCR准确率测试。OCR/患者隔离和原预约的真实页面验证另见 [U001验收汇总](../../../docs/internal/updates/U001-health-consultation/evidence/acceptance.md)。
