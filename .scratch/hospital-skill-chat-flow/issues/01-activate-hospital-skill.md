# 01: 装配并严格加载医院 Skill

Type: implementation
Status: resolved

**What to build:** 保存首方医院预约挂号 Skill，通过开发 bootstrap 幂等启用和绑定七个医院 Tool，并让 Runtime 只在 Skill 加载后暴露绑定 Tool。

- [x] 仓库包含可审查的 Agent Skills 兼容 Skill 包。
- [x] 开发 seed 幂等完成 Tool 同步、启用、Skill 创建、绑定和发布。
- [x] 未加载 Skill 前绑定 Tool 不可见，加载后在同一 turn 固定版本并开放。
- [x] 系统提示和能力状态按医院能力快照动态生成。
- [x] 生产环境不自动激活虚构医院能力。

## Answer

已增加首方医院预约 Skill 包和开发 bootstrap；七个 Tool 只在该 Skill 成功加载后进入模型工具集。系统提示与 Web 状态改为依据实时能力快照，生产启动不会自动装配测试医院。
