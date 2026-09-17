# 当前候选工作树检查

2026-09-17，明确指向D:/Projects/Agent Learn/Project/MediPet-Rebuild，分支codex/u002-semantic-intent。GitNexus index-only刷新完成：4476节点、10332边、135簇、299流程。主实现仍未提交。

临时Git索引分5批覆盖111改动路径，无error/partial/truncated；真实暂存区不变，分析期间输入文件无变动。完整输出snapshot.json；数值与秘密/忽略边界扫描见summary.json。范围外document-source-manifest.json保留且不纳入本次范围。分析后仅补本说明、汇总与执行状态。

图构建本身报告73个入口候选未纳入、611个callee分支省略、15条路径预算截断；不能把缺少图边解释成无人调用。已有上游影响证据结合当前源码核对API/主编排器/独立评测三入口、provider拥有权、实际_vote及校准调用，并有接线/时序/故障/分类API测试覆盖。变更分析无partial/truncated不等于图穷尽所有动态运行流程。

修改涉及意图识别、API及评测主流程，批次风险包含critical；已经执行相应确定性回归、真实ONNX与真实冻结响应对照。09隔离业务容器/浏览器验收没有执行，因为08质量门槛未全部通过，不以静态分析替代它。
