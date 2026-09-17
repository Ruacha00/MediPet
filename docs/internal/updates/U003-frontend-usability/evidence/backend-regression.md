# U003-07 后端回归证据

验证日期：2026-09-17。工作树：`D:/Projects/Agent Learn/Project/MediPet-Rebuild`；分支：`codex/u003-frontend-usability`；基准 HEAD：`e81f447`。运行环境为 Windows Python 3.12.7、项目 `.venv`。

本次只运行现有相关后端测试，不修改生产代码、测试文件、用户预约或前端。不启动 API lifespan，不调用实际模型，也不读取 `.env`。本记录是 U003-07 的后端验证部分，不能替代浏览器验收。

## 结果

**107 passed，5 skipped，0 failed，0 errors；pytest 耗时 59.55 秒。** JUnit 记录 112 个参数化用例，开始时间 `2026-09-17T16:16:49.946711+08:00`，suite 时间 59.483 秒。进程退出码为 0。

| 测试文件 | 通过 | 跳过 | 本次验证层级及覆盖 |
| --- | ---: | ---: | --- |
| `tests/test_chat_api.py` | 20 | 0 | 进程内 ASGI + 替身：患者/事项、历史、归档及身份拒绝、急症前置、卡片、确认/取消幂等回执、持久化失败与库存冲突 |
| `tests/test_visit_memory.py` | 17 | 0 | 替身 + 真实隔离 Redis：身份、选择上下文、消息去重、完整历史、新 Store 实例恢复与元数据无 TTL |
| `tests/test_appointment_flow.py` | 21 | 1 | 替身 + 真实隔离 Redis：准备不写库存、替代/过期、跨患者事项拒绝、确认/取消、重复回执、WATCH 并发竞争与防超卖 |
| `tests/test_conversation_memory.py` | 11 | 0 | 替身 + 真实隔离 Redis/Chroma，模型为替身：窗口恢复、患者隔离、摘要与长期记忆 |
| `tests/test_report_upload_api.py` | 8 | 0 | 进程内 ASGI、替身 Store/提取器，以及真实文字 PDF 子进程：上传身份/归档/错误边界、报告历史、保留原选择上下文 |
| `tests/test_report_preprocessing.py` | 27 | 4 | 规则、真实文字 PDF 与 OCR 替身：范围/单位/可疑数值、损坏/加密/页数/大小/像素、超时、低置信度及混合 PDF 分支 |
| `tests/integration/test_storage.py` | 2 | 0 | 真实隔离 Redis/Chroma：新连接恢复预约回执/库存；知识来源、重复导入、删除后重建与空集合 |
| `tests/integration/test_evaluation_runtime.py` | 1 | 0 | 真实隔离 Redis/Chroma + 假模型：每例数据隔离、调用计数与退出清理 |

跳过的 5 个用例保留原有条件，没有删除或改写断言：

- `test_archive_race_rejects_transaction_without_writing_appointment[real]`：原测试明确说明“归档竞态注入用替身；真实并发 WATCH 在两项事务竞争测试验证”。其替身分支通过，其他真实事务竞争用例通过。
- `test_real_scanned_pdf_and_image_ocr[PNG-scan.png]`、`[PDF-scan.pdf]`、`test_real_chinese_image_ocr`、`test_real_mixed_pdf_does_not_mistake_text_header_for_scanned_report_body`：宿主没有 Tesseract，原条件为 `real local Tesseract required`。本轮没有验证真实扫描 PDF、图片或混合 PDF 的 OCR 输出；也未用历史证据替代此次结果。

唯一 warning 是测试启动包装先导入依赖引起的 `PytestAssertRewriteWarning: Module already imported so cannot be rewritten; anyio`，不是业务断言失败。

## 运行前隔离核对

- API 测试使用 `httpx.ASGITransport` 和注入的 `MemoryRedis` / `BusinessRedis`、假 Memory/Orchestrator；不启动或调用现有服务端的 lifespan。
- 真实预约/事项测试要求专用 Redis DB15，并使用 `medipet:test:appointments:<uuid>:`、`medipet:test:m01:<uuid>:`、`medipet:test:m02:<uuid>:` 等独立前缀；fixture 只删除自身前缀。
- 真实记忆/知识测试使用随机 Chroma 集合；评测 runtime 工厂另为各 case 生成独立前缀和集合，退出时清理。未调用 Chroma 全局 reset。
- 为不接触主项目 Redis 6379 / Chroma 8001，本次新建临时 Redis 36379 / Chroma 38001。两只容器无命名卷、只用 tmpfs、带本次标签，镜像本机已存在。
- 部分现有测试将 Chroma 端口写为 8001，因此只在测试进程包装 `chromadb.HttpClient`：断言原目标是 `127.0.0.1:8001` 后映射到 38001；共实际映射 11 次连接。没有修改生产配置或测试源码。
- 在导入待测 API 前将 `dotenv.load_dotenv` 替换为无操作，阻止其模块级 `.env` 读取；进程内模型密钥使用无效测试占位。默认拦截 `AsyncMessages.create`，若意外调用真实模型立即失败。评测 runtime 测试自身显式替换为假响应，未发出真实模型请求。

## 准确执行命令

以下命令在仓库根目录的 PowerShell 中执行。端口事先检查为空闲；不得对已存在的同名容器重复运行。

```powershell
docker run -d --rm --name medipet-u003-regression-redis-20260917 --label medipet.task=u003-regression -p 127.0.0.1:36379:6379 --tmpfs /data redis:7-alpine redis-server --appendonly no
docker run -d --rm --name medipet-u003-regression-chroma-20260917 --label medipet.task=u003-regression -p 127.0.0.1:38001:8000 --tmpfs /chroma/chroma -e IS_PERSISTENT=TRUE -e PERSIST_DIRECTORY=/chroma/chroma -e ANONYMIZED_TELEMETRY=FALSE chromadb/chroma:0.5.23
```

实际测试入口如下；心跳检查成功后只运行一次，没有失败重跑或调参：

```powershell
@'
import os
from unittest.mock import patch
import urllib.request
import json
import pytest
import chromadb
from anthropic.resources.messages import AsyncMessages
os.environ['ANTHROPIC_API_KEY'] = 'test-disabled'
os.environ['MEDIPET_VISIT_TEST_REDIS_URL'] = 'redis://127.0.0.1:36379/15'
os.environ['MEDIPET_MEMORY_TEST_CHROMA'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
with urllib.request.urlopen('http://127.0.0.1:38001/api/v1/heartbeat', timeout=10) as response:
    assert response.status == 200
original_http_client = chromadb.HttpClient
transport_calls = []
def isolated_chroma(*args, **kwargs):
    assert not args, 'unexpected positional Chroma configuration'
    assert kwargs.get('host') == '127.0.0.1' and kwargs.get('port') == 8001
    kwargs['port'] = 38001
    transport_calls.append(1)
    return original_http_client(**kwargs)
async def no_real_model(*args, **kwargs):
    raise AssertionError('Real model calls are forbidden during U003 regression')
with patch('dotenv.load_dotenv', return_value=False), patch.object(chromadb, 'HttpClient', isolated_chroma), patch.object(AsyncMessages, 'create', no_real_model):
    code = pytest.main(['tests/test_chat_api.py', 'tests/test_visit_memory.py', 'tests/test_appointment_flow.py', 'tests/test_conversation_memory.py', 'tests/test_report_upload_api.py', 'tests/test_report_preprocessing.py', 'tests/integration/test_storage.py', 'tests/integration/test_evaluation_runtime.py', '-q', '-ra', '--junitxml=.scratch/U003-backend-regression.xml'])
print(json.dumps({'pytest_exit':int(code),'chroma_connections_remapped_to_isolated_port':len(transport_calls),'dotenv_disabled':True,'real_model_sdk_forbidden':True}))
raise SystemExit(code)
'@ | .venv\Scripts\python.exe - *> .scratch/U003-backend-regression.log
$testCode = $LASTEXITCODE
Get-Content .scratch/U003-backend-regression.log -Tail 65
exit $testCode
```

本地原始输出：`.scratch/U003-backend-regression.log`、`.scratch/U003-backend-regression.xml`，按仓库约定忽略，不加入提交。Windows 管道输出里的中文 skip 原因存在编码损失；上述中文原因另与 `tests/test_appointment_flow.py:282` 源码核对，测试计数由 XML 读取。

## 清理和主服务核对

测试后 `docker exec medipet-u003-regression-redis-20260917 redis-cli -n 15 DBSIZE` 为 **0**；`chromadb.HttpClient(host='127.0.0.1', port=38001).list_collections()` 为 **0 个集合**。随后检查容器标签、tmpfs 和自动移除设置：两者标签均为 `medipet.task=u003-regression`，分别挂载 `/data` 和 `/chroma/chroma`，`AutoRemove=true`，无命名卷。

只关闭以下两只本次容器：

```powershell
docker stop medipet-u003-regression-redis-20260917 medipet-u003-regression-chroma-20260917
```

命令成功，结束后的 `docker ps` 中两只临时容器均不存在。主项目四只容器仍在运行且 healthy，未执行任何停止、重启、reset 或卷清理：

| 主项目容器 | 端口 | 结束核对的 StartedAt（UTC） |
| --- | --- | --- |
| `medipet-rebuild-api-1` | 8000 | `2026-09-17T06:06:33.725020527Z` |
| `medipet-rebuild-frontend-1` | 8088 | `2026-09-17T06:06:39.410121946Z` |
| `medipet-rebuild-redis-1` | 6379 | `2026-09-17T06:06:28.007564379Z` |
| `medipet-rebuild-chromadb-1` | 8001 | `2026-09-17T06:06:28.009698433Z` |

这些启动时间均早于本轮测试。未查询或修改用户已有预约内容。

## 首批结论边界

所选后端回归及真实隔离存储用例通过，身份/历史/预约确认/上传链路没有出现本轮测试覆盖范围内的回归。5 个跳过项如上保留。本轮未覆盖完整后端测试集、运行中 API 的外部 HTTP 部署路径、前端浏览器交互、真实模型质量、U002 holdout 或真实 OCR；这些不得从本记录推导为通过。

## 独立 OCR 补验：4 passed，0 skipped

2026-09-17 16:23（北京时间）另在临时容器中运行首批因宿主缺少 Tesseract 而跳过的 **4 项 OCR 用例，4 passed、0 skipped、0 failed、0 errors，耗时 1.63 秒**。这不是首批完整套件重跑，不与 107 相加声称同一批次通过。首批仍为 107 passed / 5 skipped；仅其中 4 个 OCR 用例取得以下独立真实结果。

### 环境核对与边界

- 使用现有主 API 对应的固定镜像 `sha256:7c3d1b5de5b73e7892d49441e1bb2279c0ee0940013ff1ccb5b295cdfa0356e9`，未构建镜像或安装/下载任何依赖。探测临时容器确认已有 Tesseract **5.3.0**，语言为 `chi_sim`、`eng`、`osd`。
- 镜像没有 pytest，也没有中文测试夹具要求的字体。只读挂载宿主现有 pytest 9.1.1 的纯 Python 包、`py.py` 兼容入口；只读挂载 `C:/Windows/Fonts/msyh.ttc` 到夹具候选路径 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`。该路径仅用于使夹具找到字体，实际字体是宿主 Microsoft YaHei，不能据此声称镜像原带文泉驿字体。生产 OCR 的中文训练数据已经在镜像中。
- 当前工作树的 `health/`、`tests/` 只读挂载到容器；未挂载 `.env`、运行数据或用户卷。容器使用 `--network none --read-only`，仅 `/tmp` 为 512MB tmpfs；测试进程仍禁用 `.env` 加载并拦截真实模型 SDK。
- 首次包装启动漏挂 pytest 随附的 `py.py`，在 `import pytest` 时退出，未收集或执行测试。核对宿主文件后只补齐该只读挂载，第二次启动完成 4 项测试，没有改测试或业务断言。两次临时容器均随退出自动移除。

### 真实结果

| 用例 | 本次核对内容 | 结果 |
| --- | --- | --- |
| `test_real_scanned_pdf_and_image_ocr[PNG-scan.png]` | 实际 Tesseract 读取 PNG，原文含 `WBC` / `6.2`，类型 image，保留观察项和提示 | passed |
| `test_real_scanned_pdf_and_image_ocr[PDF-scan.pdf]` | PDF 渲染后实际 OCR，原文含 `WBC` / `6.2`，类型 pdf，保留观察项和提示 | passed |
| `test_real_chinese_image_ocr` | 中文字体合成图片实际 OCR，识别 `白细胞` 与 `6.2` | passed |
| `test_real_mixed_pdf_does_not_mistake_text_header_for_scanned_report_body` | PDF 文字层仅页眉；仍 OCR 扫描正文，保留 `Scanned report`、`WBC` / `6.2` 和未校对分区提示 | passed |

唯一 warning 为 `PytestUnknownMarkWarning: asyncio`：此独立包装禁用插件自动加载，原文件另有未选中的异步测试标记。选中的 4 项均为同步函数，没有因插件缺失跳过。JUnit suite 开始于 `2026-09-17T08:23:36.228462+00:00`，4 tests，time 1.630。原始成功输出（含每项 JUnit 结果 JSON）：`.scratch/U003-ocr-regression-2.log`；首次未收集测试的包装错误：`.scratch/U003-ocr-regression.log`。

### 补验实际命令

```powershell
$repoPath = 'D:/Projects/Agent Learn/Project/MediPet-Rebuild'
$dockerArgs = @('run','--rm','-i','--name','medipet-u003-ocr-20260917','--label','medipet.task=u003-ocr','--network','none','--read-only','--tmpfs','/tmp:rw,size=512m','--workdir','/app','-e','PYTHONPATH=/app:/test-deps','-e','PYTHONDONTWRITEBYTECODE=1','-e','PYTEST_DISABLE_PLUGIN_AUTOLOAD=1','--mount',"type=bind,source=$repoPath/health,target=/app/health,readonly",'--mount',"type=bind,source=$repoPath/tests,target=/app/tests,readonly",'--mount','type=bind,source=C:/Windows/Fonts/msyh.ttc,target=/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc,readonly','--mount',"type=bind,source=$repoPath/.venv/Lib/site-packages/py.py,target=/test-deps/py.py,readonly")
foreach ($packageName in @('pytest','_pytest','pluggy','iniconfig','packaging','pygments')) {
    $dockerArgs += @('--mount',"type=bind,source=$repoPath/.venv/Lib/site-packages/$packageName,target=/test-deps/$packageName,readonly")
}
$dockerArgs += @('--entrypoint','python','sha256:7c3d1b5de5b73e7892d49441e1bb2279c0ee0940013ff1ccb5b295cdfa0356e9','-')
@'
import os,json,xml.etree.ElementTree as ET
from unittest.mock import patch
import pytest
from anthropic.resources.messages import AsyncMessages
os.environ['ANTHROPIC_API_KEY']='test-disabled'
async def no_real_model(*args,**kwargs):
    raise AssertionError('Real model calls are forbidden during U003 OCR regression')
print('PYTEST_VERSION='+pytest.__version__)
with patch('dotenv.load_dotenv',return_value=False),patch.object(AsyncMessages,'create',no_real_model):
    code=pytest.main(['tests/test_report_preprocessing.py::test_real_scanned_pdf_and_image_ocr','tests/test_report_preprocessing.py::test_real_chinese_image_ocr','tests/test_report_preprocessing.py::test_real_mixed_pdf_does_not_mistake_text_header_for_scanned_report_body','-q','-ra','-p','no:cacheprovider','--junitxml=/tmp/u003-ocr.xml'])
root=ET.parse('/tmp/u003-ocr.xml').getroot()
print(json.dumps({'pytest_exit':int(code),'suites':[s.attrib for s in root.iter('testsuite')],'cases':[{'name':c.attrib['name'],'skipped':c.find('skipped') is not None,'failed':c.find('failure') is not None or c.find('error') is not None} for c in root.iter('testcase')]},ensure_ascii=False))
raise SystemExit(code)
'@ | docker @dockerArgs *> .scratch/U003-ocr-regression-2.log
$ocrExit = $LASTEXITCODE
Get-Content .scratch/U003-ocr-regression-2.log -Tail 70
exit $ocrExit
```

结束后 `docker ps -a --filter label=medipet.task=u003-ocr` 为空；主项目四只容器仍 healthy，StartedAt 与上表完全一致。业务源码/测试文件没有改动，用户数据未挂载。该补验覆盖合成夹具的真实本地 OCR 路径，不代表任意真实医疗报告的识别准确率，也不替代浏览器上传/部署链路验收。
