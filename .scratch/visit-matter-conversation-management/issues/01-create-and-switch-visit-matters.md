# 01: 创建并切换独立就诊事项

Type: implementation
Status: resolved

**What to build:** 为后端持久化层和 Web 增加就诊事项创建、列表与切换能力，确保每个事项使用独立上下文。

- [x] 扩展存储协议和 PostgreSQL/In-memory 实现。
- [x] 增加事项创建与列表 HTTP API。
- [x] Web 加载、创建和切换事项，并恢复对应消息历史。
- [x] 聊天和操作确认使用当前事项 ID。
- [x] 覆盖后端、前端与浏览器测试。

## Answer

实现和验收已完成，详见同目录 spec。
