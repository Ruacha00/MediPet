# 01: 分离能力定义同步与治理状态

Type: implementation
Status: resolved
Blocked by: —

## What to build

调整开发医院能力 bootstrap，使首次空 Registry 装配保持开箱可用，而后续启动只同步定义，不覆盖管理员保存的 Tool 启停、Skill 生命周期或活动版本选择。

## Acceptance Criteria

- [x] 首次空 Registry 自动启用并发布开发能力。
- [x] 已停用 Tool 在再次 bootstrap 后仍保持停用。
- [x] 已退休 Skill 在再次 bootstrap 后仍保持退休。
- [x] 已回滚到旧版本时，再次 bootstrap 不会激活最新版本。
- [x] 新 Tool 版本同步后默认停用。
- [x] 新增或发生定义变化的 Skill 保持 draft，等待显式发布。
- [x] 文档与自动化测试覆盖上述行为。

## Comments

- 2026-08-31: claimed，开始实现。
- 2026-08-31: 58 个相关后端测试、Ruff 与 Pyright 通过，issue resolved。
- 2026-08-31: 双轴 code review 发现并修复名称/描述未版本化及 `in_review` 绑定可原地变化的问题；API 全量测试再次通过。

## Answer

开发 bootstrap 现在只在 Skill 与 Tool Registry 同时为空时执行首次默认装配；已有治理状态后，启动只同步受信 Tool 定义并为新增或变化的 Skill 建立草稿。Skill 名称、描述、指令和 Tool 绑定均作为版本化定义处理，进入审核后的版本不会被同步流程原地修改。管理员保存的 Tool 停用、Skill 退休和活动旧版本选择均会跨重启保留，新 Tool 版本默认停用。README、架构说明和 ADR-0010 已记录首次装配与后续 reconciliation 的边界。
