# V05 独立容器持久化、代理与定向重置证据

日期：2026-09-16，Asia/Shanghai。工作树为 `D:/Projects/Agent Learn/Project/MediPet-Rebuild`，分支 `refactor/medipet-scenario`，HEAD `d0d2bdb`。本记录针对当前未提交实现构建出的独立验证镜像。U05 生产代码与前端构建冻结后开始本项运行；交回时已收到其负责人完成通知并核对 `done` 状态。U05 的独立管理页证据见 [management.md](management.md)，不能凭本记录代替 U05、V04 或 V06。

## 隔离环境与配置

所有 Compose 操作显式指定 **`medipet-validation`** 项目，未操作现有 `medipet-rebuild` 项目或开发 API 8010/Vite 5173。没有全库清空、删除数据卷或外部告警操作。

| 服务 | 验证宿主机端口 | 容器地址/持久卷 |
| --- | --- | --- |
| Redis | `127.0.0.1:16379` | `redis:6379/0`；`medipet-validation_redis-data:/data` |
| Chroma | `127.0.0.1:18001` | `chromadb:8000`；`medipet-validation_chroma-data:/chroma/chroma` |
| API | `127.0.0.1:18000` | `api:8000`；`medipet-validation_api-data:/app/data` |
| 前端 Nginx | `127.0.0.1:18088` | `/api/python/` 代理到 `http://api:8000/` |

使用现有 `docker-compose.yml`、Dockerfile 与前端 Dockerfile/nginx.conf，没有创建第二套应用启动流程或修改上述文件。临时 `.scratch/V05-compose.override.yml` 只覆盖四个宿主机端口，并令 API 的 `ALERT_WEBHOOK_URL` 为空：

```yaml
services:
  redis:
    ports: !override
      - "127.0.0.1:16379:6379"
  chromadb:
    ports: !override
      - "127.0.0.1:18001:8000"
  api:
    environment:
      ALERT_WEBHOOK_URL: ""
    ports: !override
      - "127.0.0.1:18000:8000"
  frontend:
    ports: !override
      - "127.0.0.1:18088:80"
```

`.scratch/V05-compose.ps1` 仅在本次调用进程移除旧的 `ANTHROPIC_API_KEY` 环境覆盖，再转发参数给：

```powershell
docker compose --project-name medipet-validation --file docker-compose.yml --file .scratch/V05-compose.override.yml <参数>
```

有效模型配置由本工作树被忽略的 `.env` 注入。仅核对非敏感配置：`deepseek-flash`、`https://api.deepseek.com/anthropic`、`MEDIPET_THINKING=disabled`，外部告警未配置。验证镜像中不存在 `/app/.env`；未将密钥写入脚本、记录或镜像文件。临时覆盖文件、探针和运行输出均经 `git check-ignore` 确认可忽略。

实测版本：Docker 29.6.2、Compose 5.3.1；容器 Python 3.12.14、Redis server 7.4.11、Redis client 5.2.1、Chroma client 0.5.23、posthog 5.4.0；Chroma 服务镜像固定为 `chromadb/chroma:0.5.23`。验证 API/前端镜像名分别为 `medipet-validation-api`、`medipet-validation-frontend`，未覆盖原项目镜像名称。默认四服务健康；可选监控仍通过 `docker compose --profile monitoring up -d` 启动，本次没有启用或占用其 9090 端口。

## 真实执行与结果

下表命令均在工作树根执行。容器 lifecycle 和代理为真实运行；夹具使用真实 Redis、Chroma 和医院服务，不调用语言模型。

| 顺序 | 实际命令/动作 | 结果 |
| --- | --- | --- |
| 配置与脚本测试 | `.scratch/V05-compose.ps1 config --quiet`；`.venv/Scripts/python.exe -m pytest tests/test_demo_reset.py -q --junitxml=.scratch/V05-reset-tests.xml` | 配置有效；**4 passed，0.76 秒** |
| 首次构建启动 | `.scratch/V05-compose.ps1 up --build -d` | 后端依赖及默认 embedding 镜像构建完成，前端 Vite 生产构建通过；四服务健康 |
| 初始夹具 | `.venv/Scripts/python.exe -B .scratch/V05-runtime-probe.py seed` | 经 Nginx 创建孩子事项；真实服务查询并准备方案；经 Nginx 确认接口写预约、回执及两条历史。库存由 5 变为 4，默认知识片段 17 |
| 二次应用启动 | `.scratch/V05-compose.ps1 restart api frontend`；`.scratch/V05-compose.ps1 up -d --wait --wait-timeout 120`；探针 `restart` | 同一预约、原始回执、完整历史和库存 4 不变；默认知识仍 17，无重复初始化 |
| 重建容器 | `.scratch/V05-compose.ps1 up -d --force-recreate --wait --wait-timeout 180 redis chromadb api frontend`；探针 `recreated` | **四个容器 ID 全部改变，挂载保持相同**。Redis 预约、历史、回执、库存和 Chroma 两份记忆标记仍可读；API 数据卷文件保留 |
| 停止应用写入 | `.scratch/V05-compose.ps1 stop api frontend` | 仅验证项目 API/前端停止，Redis/Chroma 仍运行 |
| 重置预览 | `.scratch/V05-compose.ps1 run --rm --no-deps api python -m scripts.reset_demo` | 匹配 51 个 `medipet:` 键、三个精确集合；删除数 0 |
| 执行重置 | 同上追加 `--execute`；探针 `reset_empty` | 删除 51 个项目键和三个精确集合；实际断言项目范围为空，外来数据保留 |
| 重复重置 | 再次运行上述 `--execute` | 删除 0 键、0 集合，正常退出 |
| 恢复默认数据 | `.scratch/V05-compose.ps1 up -d --wait --wait-timeout 180`；探针 `reset_initialized` | 普通 lifespan 重新初始化：两位预置患者、17 个知识片段、库存恢复 5；事项和预约为 0，情景/画像集合为空；外来数据仍保留 |

每个可运行阶段均从 `http://127.0.0.1:18088` 检查首页、`runtime-config.js` 及 `/api/python/health` 返回 200。真实事项创建、确认及完整历史读取都经过这个同源代理，不是直接访问 API 的替代证明。业务键 TTL 为 `-1`，没有继承工作窗口的过期时间。

夹具在 `medipet_episodic`、`medipet_profiles` 分别写一份固定 embedding 的测试记忆标记，用于证明 Chroma 卷持久化；它不验证真实摘要/画像模型质量。API 数据卷另放 `eval/candidates/v05-preserved-marker.json`，内容明确为持久化测试标记，不是评测报告；重建和重置后字节内容保持一致。

## 重置保留范围

[scripts/reset_demo.py](../../../../scripts/reset_demo.py) 默认只预览，`--execute` 才删除。固定目标是：

- Redis：`SCAN` 匹配 **`medipet:*`** 后按返回键分批删除；不调用全库清空。
- Chroma：仅精确名称 **`medipet_knowledge`、`medipet_episodic`、`medipet_profiles`**；不按 `medipet_` 通配清理，也不调用全局 reset。
- 文件：不删除 `/app/data/eval`、候选/基线文件、日志或卷。测试中的候选目录标记保留，说明这类文件不在重置范围。

同一真实 Redis 内的 `external:v05:marker`、`medipet_external:marker` 均保留原值；同一 Chroma 的 `external_v05_collection`、`medipet_external` 均保留文档。这既检查明显外来名称，也检查相近前缀不会误删。替身测试额外确认 `medipet_eval_fixture_profiles` 不属于删除目标。脚本先检查两种存储连接，再开始删除；删除跨两种存储不构成分布式事务，发生中途失败可再次执行并查看实际结果。

供 V08 使用的同一 Compose 启动路径命令如下，必须先停止该目标项目的应用请求：

```powershell
docker compose stop api frontend
docker compose run --rm --no-deps api python -m scripts.reset_demo
docker compose run --rm --no-deps api python -m scripts.reset_demo --execute
docker compose up -d
```

预览本身不清理；执行后由普通 API 启动补默认患者、号源和文档，不在重置脚本复制另一套初始化算法。真实验证时每行都使用前述 `medipet-validation` 项目与覆盖参数，未对默认项目执行这些操作。

## 证据位置与剩余边界

- 本地原始断言：`.scratch/V05-runtime-results.json`，包含 seed、restart、recreated、reset_empty、reset_initialized 五阶段，全部 `passed=true`；对应探针为 `.scratch/V05-runtime-probe.py`。
- 四个容器重建前后 ID 与挂载：`.scratch/V05-containers-before.txt`、`.scratch/V05-containers-after.txt`。脚本断言四个 ID 均变化、各挂载 JSON 不变。
- 真实测试结果为 `.scratch/V05-reset-tests.xml`；新增源码与测试分别为 [reset_demo.py](../../../../scripts/reset_demo.py)、[test_demo_reset.py](../../../../tests/test_demo_reset.py)。检查既有 `medipet-rebuild` 四容器仍健康且运行时长未重置；开发服务端口未操作。独立验证容器在交回时保持健康，数据卷未删除。
- GitNexus 已显式绑定当前工作树：索引 2026-09-16T07:47:37.679Z、HEAD d0d2bdb、2639 nodes。新增脚本未在旧索引中，结果 UNKNOWN；源码检索确认没有现有消费者。关联 `initialize_patients` 为 LOW/receiverTyping 下界，补核 API lifespan 与评测工厂；`ensure_slots` 精确目标为 HIGH，`_load_default_docs` 为 LOW。这些生产方法均未修改，已通过本次真实启动与持久化断言核实。没有重建索引或提交。
- 本次未运行真实 LLM，也不代替浏览器交互、正式模型评测。`frontend/nginx.conf` 当前普通代理读取超时 180 秒；完整真实评测耗时由 V06 实测核对。若超过该值，仅对 `/api/python/eval/run` 延长有限超时，由后续实际结果决定；没有重复发起完整评测或预先声称长评测代理通过。

## V06 后续代理修正

首次完整真实评测实际 HTTP 耗时269.062秒，超过原180秒同步代理等待上限。主 agent 因此在 `frontend/nginx.conf` 增加仅匹配 `/api/python/eval/run` 的精确路由，等待上限1800秒；其他请求仍180秒，同源路径及后端接口不变。修改前 GitNexus API影响为 LOW/0静态消费者，已源码核对 `runEvaluation` 与页面评测按钮实际消费，不能把0视为未使用。

第二份完整评测已通过重建后的验证容器前端 `http://127.0.0.1:18088` 单次点击运行，经同源代理到 API 18000，使用默认54个意图、12个多轮和12个边界案例。实际 HTTP 返回200，耗时 **170.344秒**，页面错误0，运行前后62份源文件 SHA 均未变化。完整结果及展示证据见 [第二份报告说明](../../../../evaluation/reports/live-20260916-container/notes.md)。这证明重建后的容器链路完成了整次请求；170.344秒低于原180秒上限，不能声称本次实际跨过了该等待边界。

候选报告 `a13acc91e787406bb909067a456be4a3` 保存在验证项目的 `api-data` 卷。容器候选与 HTTP 响应的规范 JSON SHA 一致，baseline 在运行前后均不存在，报告仍为 `candidate`、`accepted_by=null`，人工复核待完成；见 [容器运行后核对](../../../../evaluation/reports/live-20260916-container/container-after.json)。上述 V05 独立持久化与重置验证是此前的历史记录，不能替代这份真实模型报告。

## 后续主项目运行状态

据 [执行检查点](../EXECUTION.md)，主项目 `medipet-rebuild` 的最终 API 与前端已重建：8088首页及同源健康检查均返回200，患者入口可用，页面错误0；对应 Redis 6379、Chroma 8001、API 8000。旧开发 API 8010 与 Vite 5173 已在核对进程身份后停止，日常使用 README 中唯一 Compose 入口8088。

独立验证项目仍保留 Redis 16379、Chroma 18001、API 18000、前端18088及其独立数据卷，第二份候选报告保存在该项目的 `api-data` 中。本节同步既有执行记录，未再次运行服务、模型或测试。
