# 01: 建立独立虚构医院数据源并实现 FakeHospitalOperations

**What to build:** 按本功能规格建立可校验、可注入时钟的虚构医院 JSON 数据源，实现 typed `HospitalOperations` 查询与创建预约提交，并通过公开接口验证患者隔离、幂等、故障和并发语义。

**Type:** implementation

**Status:** resolved

- [x] 独立 JSON 包含唯一服务医院、4 个科室、医生、未来两周相对排班和初始占号。
- [x] 公开查询覆盖医院资料、科室、医生、可用号源、单个预约和患者预约列表。
- [x] 创建预约保存费用快照并使号源不再可预约。
- [x] 同键同参返回同一收据，同键异参拒绝。
- [x] 并发抢号只成功一次，失败提交不留下副作用。
- [x] JSON 引用和排班错误在初始化时失败。
- [x] 不接入 Tool、Skill、Runtime、PostgreSQL seed 或前端。

## Answer

已建立独立只读 JSON 数据源和 typed `HospitalOperations` 边界，实现患者范围内的目录、号源与预约查询，以及具备费用快照、原子占号和医院侧幂等语义的创建预约操作。确定性查询/提交故障、严格数据校验与 Windows 无系统 tzdata 时的 `Asia/Shanghai` UTC+8 fallback 均已覆盖；完整 API 测试、Ruff 和 Pyright 通过。
