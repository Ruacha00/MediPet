# U001 实施计划

依据：[更新意图](intent.md)。状态以 issues 文件为准。

## 实施路径

| Spec | 交付 | 原子任务 |
| --- | --- | --- |
| [S01 意图与编排](specs/S01-routing.md) | 三个意图、两个 Agent、急症前置、工具隔离 | U001-01—04 |
| [S02 分诊与药品知识](specs/S02-information.md) | 有来源的分诊/药品信息及常驻规则 | U001-05—07 |
| [S03 报告与界面](specs/S03-reports.md) | 文本、PDF、图片预处理及历史卡片 | U001-08—11 |
| [S04 验证交付](specs/S04-validation.md) | 回归、真实组件/模型验证及文档 | U001-12—14 |

## 依赖与调度

| Issue | 结果 | 直接依赖 | 文件负责人 |
| --- | --- | --- | --- |
| [U001-01](issues/U001-01.md) | 主分支、范围与共享契约 | 无 | root |
| [U001-02](issues/U001-02.md) | 意图定义、模板、实体与分类 | 01 | root |
| [U001-03](issues/U001-03.md) | 两个 Agent、路由与工具调用 | 02、05、06、08 | root |
| [U001-04](issues/U001-04.md) | 急症识别与固定响应 | 01 | audit_preparation_scope |
| [U001-05](issues/U001-05.md) | 症状分诊规则与来源 | 01 | spec_agents_knowledge |
| [U001-06](issues/U001-06.md) | 药品信息与组合查询 | 01 | spec_agents_knowledge |
| [U001-07](issues/U001-07.md) | 领域知识与 Skills | 05、06 | spec_agents_knowledge |
| [U001-08](issues/U001-08.md) | 报告文本结构化 | 01 | spec_api_ui |
| [U001-09](issues/U001-09.md) | PDF 与图片文字提取 | 08 | spec_api_ui |
| [U001-10](issues/U001-10.md) | 报告 API 与患者事项历史 | 09 | spec_api_ui |
| [U001-11](issues/U001-11.md) | 上传入口与医疗卡片 | 03、10 | spec_api_ui |
| [U001-12](issues/U001-12.md) | 组件与接口集成回归 | 03、04、07、11 | root |
| [U001-13](issues/U001-13.md) | 真实 OCR、容器、模型与浏览器 | 12 | root 协调 |
| [U001-14](issues/U001-14.md) | 使用文档及最终变更核对 | 13 | root |

01 完成后，02/04/05/06/08 可按独占文件并行；同一负责人内部有序推进。接口不变时允许 UI 提前按契约实现，只有依赖验证结束后才能关闭 issue。03 依赖服务的已验证输出，不等待整个 UI 完成。

## 共享文件和循环控制

- root 独占 core/intent_recognizer.py、agents/、hospital/models.py、health/models.py、更新计划与检查点。
- spec_agents_knowledge 独占 health/triage.py、health/medications.py、health/data/、knowledge/、skills/ 及对应新测试。
- spec_api_ui 独占 health/reports.py、health/report_upload.py、api/main.py、frontend/、Dockerfile、依赖清单及对应新测试。
- audit_preparation_scope 独占 core/emergency.py 及急症测试；旧意图测试只编辑急症相关断言，其他段落由 root 维护。
- 总并发最多 root + 3 子 agent；复用已有 agent，不递归派生。跨文件问题先交给负责人，不抢写。
- 同类失败连续两次先诊断；模型演示有固定用例/次数，不用循环重跑直到“全绿”。索引刷新由 root 串行执行。
- 压缩前更新 EXECUTION：已完成结果、待办、文件所有权、后台进程和证据位置。旧源码与另外工作树保持原状。

## 验收

新功能必须有正常、未知资料、缺失输入和失败用例；急症须证明零普通模型调用。药品事实和报告数值来自可核对输入，不能由生成文字反推事实。真实 OCR 使用合成报告而非个人医疗资料，覆盖文字 PDF、扫描 PDF 和图片。真实模型覆盖分诊、药品、报告和预约回归；报告保存 candidate，不自动接受新旧基线。

全量后端、前端测试和构建完成后做真实组件与浏览器验证，最后更新当前树索引、执行完整变更分析，报告未完成项。未跟踪的文档来源清单与“文档+简历”属于其他工作，不纳入本次更新。
