# 04: 生成提交固定的策略证据

**What to build:** 为已经完成的症状选科边界实现生成可复现、明确指向 implementation commit 的最终评估证据，使维护者可以区分当前结果和历史报告。

**Blocked by:** 03: 锁定医院协助与离线策略回归.

**Status:** resolved

- [x] 从已提交且工作树无实现改动的 implementation commit 开始，确认该提交包含最终策略、集成、语料、测试和架构说明。
- [x] 在该 implementation commit 上运行聚焦后端测试、完整后端回归、策略 fake 评估、既有 fake 核心评估、Ruff 和 Pyright；需要 PostgreSQL 的完整验证使用现有 Ubuntu CI 边界。
- [x] 新策略 fake 评估全部通过，人工导诊和紧急转介案例保持零模型请求和零 Tool 调用，允许案例满足声明的 Skill、Tool、消息部件和步骤预算。
- [x] 只生成新的机器可读和人类可读策略基线报告，不修改任何已有 live、targeted 或 fake 历史报告。
- [x] 两份新报告记录的 Git commit 与被验证的 implementation commit 完全一致，并包含相同的案例数、通过数和关键预算结果。
- [x] 报告生成后不再修改实现、测试、语料或架构说明；若这些内容发生变化，返回上一票重新建立新的 implementation commit。
- [x] 对仅包含报告的最终变更运行完整且非 partial、非 truncated 的 GitNexus change analysis，并确认没有未审查的直接依赖。
- [x] 将报告作为独立 evidence commit 保存，使报告中的 commit 继续指向其实际验证的 implementation commit。

## Answer

在干净的 implementation commit `cf068996884ae5c4111eb87e492c350473b4a8b9` 上完成 Windows 本地回归与 Fake Eval，并生成 7/7 通过、零预算超限的 JSON/Markdown 策略基线；报告已由独立 evidence commit `6e78c54` 保存。
