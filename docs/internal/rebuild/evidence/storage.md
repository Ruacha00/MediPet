# V03 真实 Redis 与 Chroma 验证

验证时间：2026-09-16 15:29—15:31（Asia/Shanghai）。工作树为 `D:/Projects/Agent Learn/Project/MediPet-Rebuild`，分支 `refactor/medipet-scenario`，HEAD 为准备提交 `d0d2bdb`；以下结果针对当前未提交的实现与测试。

## 环境与隔离

| 组件 | 实测版本与连接 |
| --- | --- |
| Python | 3.12.7，工作树 `.venv/Scripts/python.exe` |
| Redis 服务 | 7.4.11，Compose `redis:7-alpine`，`127.0.0.1:6379` 的专用 DB15 |
| Redis 客户端 | 5.2.1，与 `requirements.txt` 一致 |
| Chroma 服务与客户端 | 均为 0.5.23，Compose `chromadb/chroma:0.5.23`，HTTP `127.0.0.1:8001` |
| posthog | 5.4.0，与固定依赖一致 |

Redis 和 Chroma 容器状态均为 healthy。服务版本分别由 Redis `INFO server` 与 Chroma `get_version()` 读取，客户端版本来自当前虚拟环境。测试使用随机 UUID 的 `medipet:test:*` Redis 前缀及 `medipet_test_*` Chroma 集合；收尾只删除各测试自身的键与集合。运行后只读核对上述测试范围，Redis 残留键和 Chroma 残留集合均为 0。未清空数据库、重置正式集合或重启容器。

## 运行方式与结果

本地进程已配置 `MEDIPET_VISIT_TEST_REDIS_URL` 指向专用 DB15（连接凭据不写入本证据），并设置 `MEDIPET_MEMORY_TEST_CHROMA=1`。在工作树根运行：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_appointment_flow.py tests/test_visit_memory.py tests/test_conversation_memory.py tests/integration/test_storage.py -k real -q --junitxml=.scratch/V03-storage.xml
```

结果：**19 passed, 1 skipped, 29 deselected，32.64 秒，退出码 0**。本地原始报告为 `.scratch/V03-storage.xml`，按仓库规则不纳入提交。

其中 **18 项通过使用真实 Redis/Chroma**。筛选表达式 `-k real` 也命中了名称含 `real_list` 的 `test_selection_requires_current_real_list_and_reselection_supersedes[fake]`，这一项使用内存替身，单独计算，不作为真实存储证据。

| 真实检查 | 通过数 | 证据与断言 |
| --- | --- | --- |
| 预约方案、确认与取消 | 10 | `tests/test_appointment_flow.py` 的 `[real]` 参数：准备阶段不扣库存；过期、改选、患者/事项冲突拒绝执行；创建/取消重复确认返回原回执；资料变化或无号源不半写入；同方案并发只写一条预约；两方案竞争最后一个号源不超卖；并发取消只恢复一次库存。并发用例在两次 EXEC 前同步放行，实际触发 Redis WATCH 冲突。 |
| 完整历史与事项元数据 | 2 | `tests/test_visit_memory.py` 的真实用例：消息去重、业务卡片、最近选择、重命名与归档恢复在新 Store 实例可读；持久业务键 TTL 为 -1。 |
| 工作、情景及画像记忆 | 4 | `tests/test_conversation_memory.py` 的 `[real]` 参数：工作窗口压缩为摘要与最近 5 条；真实 Chroma 先查当前事项再回退同患者其他事项，排除另一患者；患者资料与参与者表达偏好按职责隔离；实际 Redis 过期后恢复完整历史中的最近窗口、业务卡片和选择，完整消息不丢失，归档恢复后仍可读。 |
| 知识库与普通重新初始化 | 2 | `tests/integration/test_storage.py`：真实 HTTP 集合反复初始化不重复导入；查询带稳定来源信息；重复上传去重；删除一个默认文档片段后再次初始化补齐；独立测试集合清空后检索返回空列表。关闭并重新连接 Redis、重建服务、重新初始化后保留预约回执和已扣库存，业务键没有 TTL。 |

跳过项为 `test_archive_race_rejects_transaction_without_writing_appointment[real]`：该测试的归档时机注入只适用于内存替身，测试文件明确跳过真实参数。它不计入真实通过数；实际 WATCH 并发由上表三个事务竞争用例验证。本轮未声称验证真实存储下的归档并发时序。

记忆测试中的摘要/画像模型使用确定性替身；Chroma 存储、过滤和默认客户端 embedding 实际运行。本证据只证明存储语义，不作为真实语言模型、浏览器或容器重建持久化的证据。容器重启/重建后的持久化由 V05 验收。

## 失败修正与分析边界

接手前一次运行结果为 18 passed、1 skipped、1 failed。失败测试向知识库传入空资料目录，而生产初始化明确要求存在医院知识文档，返回 `ValueError: 未找到医院知识文档`。本轮仅修正测试：用独立测试集合正常初始化，再删除该集合自身片段验证空检索；默认文档补齐测试按 `metadata.source` 的 `knowledge/` 前缀选片段，避免误删上传文档。不修改生产知识库契约。

修改前的 GitNexus upstream 查询显式绑定本工作树，目标是新增集成测试函数。当前索引尚未包含该文件，结果为 `UNKNOWN / Target not found`，不是无影响结论。当前源码引用搜索仅找到测试定义；本轮修改限定此测试文件与 V03 记录。统一索引更新及提交前变更分析由主 agent 串行执行。
