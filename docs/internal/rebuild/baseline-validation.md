# P1 基准验证记录

日期：2026-09-16。工作树：`D:/Projects/Agent Learn/Project/MediPet-Rebuild`；分支 `refactor/medipet-scenario`，准备提交 `d0d2bdb` 后的未提交实施代码。

## 来源与依赖

固定来源与必要差异见 [后端来源](source-map.md)、[前端来源](frontend-source-map.md)。源目录的密钥、运行数据和依赖目录未导入。

- 宿主 Python 3.12.7，独立 `.venv`；Node 22.20.0 / npm 11.11.1。
- 应用依赖沿用基准；增加 `posthog==5.4.0` 约束。Chroma 0.5.23 调用旧式三个位置参数，自动安装的 posthog 7.54.0 报 telemetry 参数错误；5.4.0 经实际 PersistentClient 验证消除错误。
- 测试依赖：pytest 9.1.1 / pytest-asyncio 1.4.0。
- A01 日期接入时实测 Windows 的 `ZoneInfo('Asia/Shanghai')` 缺少系统时区库，补充 `tzdata==2026.4`；安装后该时区加载通过。此项来自实际缺失依赖，不修改日期算法。
- Docker 29.6.2 / Compose 5.3.1；Redis 7、Chroma 0.5.23、Python 3.12 和 Node 22 构建镜像，Nginx 提供前端与代理。
- 前端锁文件保持基准依赖；已知审计结果 1 low / 2 high，未在导入阶段扩展依赖升级范围。

## 分层结果

| 层次 | 已执行检查 | 结果与边界 |
| --- | --- | --- |
| 源码核对 | 固定提交导入、关键文件 SHA-256、运行品牌与入口检查 | B01/B02 完成；后续医院业务尚未实现 |
| 假模型 | `.venv/Scripts/python.exe -m pytest -q tests/test_agent_orchestrator.py` | 12 passed in 0.92s；覆盖既有角色、路由、工具往返/白名单、三次请求上限，以及事件屏障证明的并行与整合降级 |
| 前端 | `npm ci`、`npm run build`、B02 五项请求/代理检查 | 构建与 5/5 检查通过；11 个请求方法，Python 单入口 |
| Compose | `docker compose config --quiet`、`up --build -d`、`up -d --wait` | 四个服务均 healthy；首次构建已缓存客户端 ONNX embedding 模型 |
| API 与容器代理 | API 8000 `/health`、前端 8088 `/api/python/health`、`runtime-config.js` | 两次 HTTP 200 / status=ok，4 个 Agent；配置 `/api/python` |
| 真实存储连接 | Redis 客户端 PING、Chroma HttpClient heartbeat/list_collections | 均成功；基准 collection 已初始化。只证明连接，不代替后续事务与患者过滤验证 |
| 真实向量 | 默认 ONNX MiniLM 对演示文本编码 | 1 个 384 维向量；HTTP 模式同样由客户端计算，容器构建预缓存模型 |
| 真实模型协议 | anthropic 0.40.0 → DeepSeek 兼容 Messages，两次模型请求 | `deepseek-flash` 成功生成工具调用、接收工具结果并回复预期文本；总输入 460 / 输出 72 tokens |
| 持久化配置 | Redis AOF、Chroma/API 命名卷；更新模型配置后重建 API | 容器重建沿用同一卷，未清空数据；预约及完整历史的重启验收在 V05 |

本地过程日志保存在被忽略的 `.scratch/B03-compose-build.log`、`B03-runtime-verification.json` 和 `B03-posthog.log`；这里记录可提交的结果，不包含密钥。实际模型的医院闭环、质量评测与浏览器业务验收仍属后续任务。

## DeepSeek 适配证据

用户选择 `deepseek-flash`，本地配置使用 `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`。保留现有 SDK 与自动工具选择，未引入供应商抽象层。依据 [DeepSeek 官方兼容说明](https://api-docs.deepseek.com/guides/anthropic_api/)，其支持 Messages 与 tool_use/tool_result。

首次认证错误来自终端中已有的另一份 `ANTHROPIC_API_KEY` 覆盖 `.env`；仅清除本次启动进程的旧值后认证成功。验证脚本显式读取本工作树 `.env`，Compose 重新创建 API 载入正确配置。未修改用户全局环境。

强制指定工具的探测返回 `Thinking mode does not support this tool_choice`；按项目既有行为使用自动工具选择后完整往返成功。因此不在业务代码添加强制 tool_choice，也不因此更换 SDK。

## GitNexus

当前索引只针对本工作树，工具 `repo` 必须传绝对路径，避免与原项目同名注册混淆。初始导入图为 1396 nodes / 2584 edges / 91 flows。流程提取存在分支上限，UNKNOWN 或未覆盖结果结合源码引用核实。

FTS 的 File/Method 两张表遇到 Windows 检查点错误；单独修复未成功，doctor 确认图和 FTS 扩展可用。暂停本工作树图查询后，执行 `gitnexus analyze <本工作树> --index-only --force --verbose`，完整重建成功：**1427 nodes / 2596 edges / 34 clusters / 91 flows**，2026-09-16 06:20:27 UTC。

`meta.json` 的 `capabilities.graph/fts.status` 均为 `available`，真实 `query("ResponseComposer compose")` 通过 BM25 返回本工作树 `agents/agent_orchestrator.py:569` 的 `ResponseComposer.compose`。因此全文查询已恢复；向量检索扩展未启用，不将其写成已验证。索引和诊断产物保持忽略。
