# 真实 LLM Runtime 与空能力管理平台

Type: map

| Ticket | Status | Blocked by |
| --- | --- | --- |
| 01 接通真实模型流式对话 | resolved | — |
| 02 持久化并恢复就诊事项对话 | resolved | 01 |
| 03 支持开发环境 `.env` 热加载 | resolved | 01 |
| 04 完成零能力 ReAct Runtime | resolved | 01 |
| 05 管理版本化指令 Skills | resolved | 02, 04 |
| 06 导入导出并隔离 Skills | resolved | 05 |
| 07 同步并治理受信 Tools | ready-for-agent | 02, 05 |
| 08 暂停并确认有副作用 Tools | ready-for-agent | 02, 07 |
| 09 加固流取消、重试和预算 | ready-for-agent | 02, 04 |
| 10 可观测性、基准和发布验证 | ready-for-agent | 03, 06, 08, 09 |
