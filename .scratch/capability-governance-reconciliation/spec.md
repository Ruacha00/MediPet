# 能力定义同步与治理状态分离

Type: spec
Status: resolved

## Problem Statement

开发环境启动时会从 `capabilities/` 同步医院 Skill 与 Tool 定义，但当前 bootstrap 每次都会启用全部 Tool、发布最新 Skill，并重新激活已退休或已回滚的版本。这样会让一次普通重启覆盖管理员通过 Registry 作出的治理决定。

## Solution

把开发环境的首次能力装配与后续定义同步分开：空 Registry 首次装配时仍启用并发布仓库自带的开发能力；已有 Registry 后只同步新定义和创建待审核版本，不改变已有 Tool 启停、Skill 发布、退休或活动版本状态。新契约版本默认停用，新 Skill 或变更后的 Skill 版本保持草稿，等待管理 API 显式治理。

## Acceptance Criteria

- 空 Skill/Tool Registry 首次启动后仍能得到可运行的开发医院能力。
- 重启不会重新启用管理员已经停用的 Tool。
- 重启不会重新激活已退休的 Skill，也不会覆盖管理员选择的活动旧版本。
- 新 Tool 契约版本被同步但默认停用。
- 新 Skill 或外置 Skill 内容、绑定变化会创建草稿版本，不会自动发布。
- 同步和治理行为继续写入现有审计记录。
- README 清楚说明首次装配和后续同步的区别。

## Out of Scope

- Skill/Tool 管理 Web 界面。
- 生产身份、角色和审批体系。
- 任意 HTTP、MCP、OpenAPI 或上传代码形式的 Tool。
