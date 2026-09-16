# U001 报告预处理、上传与医疗卡片证据

日期：2026-09-16。基准 `c712e87`，工作分支 `codex/u001-health-consultation`。本记录区分本地真实提取、假存储接口测试和根 agent 执行的容器浏览器验收。本地测试未调用付费模型；不把局部测试计为完整医疗效果评测。

## 已落地行为

- `health/reports.py::preprocess_report` 保留完整提取文字和每项原始行，仅对原文中同时明确给出的数值、单位和闭合参考区间做 Decimal 比较。缺单位、缺区间、倒置区间、疑似 OCR 字符、规则外排版保留为 `unassessed`，不补造疾病或治疗结论。支持负数区间和上/下界相等。
- `health/report_upload.py` 本地处理 PDF、PNG、JPEG、WebP：纯文字 PDF 用 pypdf；无文本或包含图片的混合页用 PDFium 渲染后调用 Tesseract `chi_sim+eng`；图片直接 OCR。混合页保留准确的文字层原文，OCR 补充明确标注“未校对”，仅删除与原文完全相同的重复行，不修复/替换数值。不使用外部 OCR API 或视觉模型，不保存原件。提取结果和警告供用户对照原件核对。
- 单文件 10 MiB、PDF 1–10 页、单页或单图 1600 万像素、文字 100000 字符；OCR 每页 10 秒、工作进程总计 120 秒、同时最多 2 个工作进程。Linux 工作进程地址空间上限 768 MiB，超时结束其进程组；Windows 结束已知工作进程及其子进程。PDFium 调用只在独立进程内运行。任一 OCR 词置信度低于 60 时，本次全部数值退回待核对，不据不可靠文字比较区间。
- 格式、内容与扩展名不符，损坏、加密、页数/像素/大小超限、无文字和超时分别返回明确错误。Nginx 上传路由允许 11 MiB 请求体用于 10 MiB 文件及 multipart 边界，API 单独限制文件大小。
- `POST /reports/preprocess` 复用现有身份加载及事项校验；拒绝其他用户/患者和归档事项，提取结束时再次由历史存储校验事项状态。用户上传记录与 `report_summary` 卡片写入同一事项完整历史；只失效工作记忆，不覆盖号源选择或预约上下文。元数据使用 `processing=report_preprocessor`，界面显示“报告整理”，不伪造 Agent 执行。
- 前端新增上传入口和 `triage_guidance`、`medication_info`、`report_summary` 三类卡片。展示科室理由、缺失信息、药品说明分节、来源链接及日期、报告具体值/单位/原区间/状态和全部提取文字。延续原深色页面样式。患者切换后丢弃旧事项晚返回的上传结果，重新读取相应完整历史。

## 依赖与上游依据

固定 `pypdf==6.18.1`、`pypdfium2==5.13.0`、`Pillow==12.3.0`。开发依赖继续通过 `requirements-dev.txt` 引入生产清单。镜像安装 `tesseract-ocr` 与 `tesseract-ocr-chi-sim`；OCR 直接使用 CLI，不增加 pytesseract。

- [pypdf 文本提取文档](https://pypdf.readthedocs.io/en/latest/user/extract-text.html)：文字提取与扫描图像 OCR 是不同路径。
- [pypdfium2 使用文档](https://pypdfium2-team.github.io/pypdfium2/readme.html)：渲染与资源释放，PDFium 不支持并发线程调用，因此使用独立进程。
- [Pillow 图片文档](https://pillow.readthedocs.io/en/stable/reference/Image.html)：图片读取、验证及解压尺寸限制。
- [Tesseract 命令行文档](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)：选择中英文语言和 TSV 输出，使用 TSV 中实际词置信度。

本地实测工具为 Windows Tesseract `5.5.0.20241111`，已安装 `chi_sim` 与 `eng`；仅为测试进程追加其 PATH，没有修改项目 `.env`。

## 已执行验证

2026-09-16 最新局部后端命令：

```powershell
$env:PATH = 'C:/Program Files/Tesseract-OCR;' + $env:PATH
.venv/Scripts/python.exe -m pytest tests/test_report_preprocessing.py tests/test_report_upload_api.py -q --tb=short --junitxml=.scratch/u001-reports.xml
```

最初结果为 36 passed；混合 PDF 回归补充后的最新结果为 **39 passed，4.00 秒，0 skipped**。具体覆盖：

- 真实文字 PDF 直接提取，无 OCR；真实工作子进程读取文字 PDF；真实子进程经过 HTTP API 后保留实际数值和 `within` 标记。
- 真实扫描 PDF、英文 PNG 和中文 PNG 经本地 Tesseract 提取，核对 `WBC`/`白细胞` 与 `6.2`，保留待核对警告。
- 真实“Scanned report”文字页眉加扫描正文的混合 PDF，确认不再只提取页眉而漏掉报告。额外回归验证文字层准确数值保留、只去除完全相同的 OCR 重复行、低置信度结果保持 `unassessed`。
- 规则正确/错误区间、缺单位、负数、混合格式和 OCR 可疑字符；空文件、假扩展名、MIME 不符、损坏/加密 PDF、11 页 PDF、图片像素上限、无文字、OCR 超时/失败/低置信度。
- 隔离内存存储和假提取器验证身份拒绝、归档拒绝、识别期间归档后不写历史、历史卡片一致、选择状态不变、读取最多上限加一字节。以上不冒充真实 Redis 验证。

前端使用已安装 Node/Vue 依赖执行 `node --test --test-reporter=dot tests/*.test.mjs`：**35 passed**。`vite build`：通过，20 个模块。新增检查包含三类卡片实际字段、观察值及标记、安全来源链接、文件输入边界和异步上传期间切换患者后恢复正确事项历史。构建产物与 node_modules 继续忽略。

## 当前树影响分析

使用绝对仓库路径 `D:/Projects/Agent Learn/Project/MediPet-Rebuild`，索引 SHA 为 `c712e87`。`requestJson` CRITICAL（17 直接调用）、`readHistory` CRITICAL（8 直接调用）、`beginContext` CRITICAL（5 直接调用）；这些共享函数保持签名与实现不变，新上传函数复用其请求/身份机制。`agentLabel` LOW，增加两个新角色。`_load_visit`、`append_messages` 查询为 LOW，但静态接收者类型及跨语言边存在遗漏，已按当前源码复核 `/chat`、确认与前端调用链，不把图中空调用视为未使用。根 agent 已说明 HIGH/CRITICAL 影响，最终索引和提交前变更分析由根 agent 统一完成。

## 容器与浏览器阶段记录

根 agent 已从 `http://127.0.0.1:18088` 执行一次真实页面流程，[原始结果](browser-health/health.json)保留第一次中止状态：成年分诊、药品、粘贴报告、三类上传、刷新、同事项报告追问和前后端大小上限共 9 组检查通过。切到儿童的新事项后，模型返回无报告的安全澄清，未返回卡片或工具调用；脚本原先强制要求一次失败工具调用，导致验收中止。该中止是过严的脚本条件，不是发现报告跨患者泄漏；其后的跨患者上传和宽度检查在这份原始结果中尚未执行。

脚本按一次顺序验证成年咳嗽科室参考、布洛芬/华法林、粘贴报告、真实图片/扫描 PDF/文字 PDF 上传、刷新、同事项报告追问、切换患者后无报告、跨患者上传拒绝、10 MiB 前后端错误及 390/768/1440 宽度布局。已有报告的追问必须实际调用 `read_current_report`，卡片观察值必须与最近上传结果完全相同。另一患者无报告时，允许明确要求上传/提供报告的安全澄清；必须没有报告卡片、没有成功读取报告的 trace、没有上一患者的项目和数值，仍保留跨患者上传 403 与页面隔离检查。

调整后的隔离条件已对第一次保存的实际响应做离线复核并通过，新增真实请求为 0，原始 `health.json` 没有修改。混合 PDF 修复后尚未据此声称容器通过；根 agent 继续统一部署与后续验收。运行时保存完整 HTTP 结果、失败信息、页面错误、耗时和截图，使用的报告均为合成数据，不自动重试或改模型参数。

后续全量评测脚本 `run_evaluation.py` 的固定输入检查已按 U001 更新为 **66 意图 / 15 对话 / 12 边界**，源指纹加入 `health/`（包括 `health/data/*.json`）。影响分析 `source_manifest` 为 LOW；常量 `EXPECTED_COUNTS` 图结果 UNKNOWN，经源码确认只由输入前检与响应完整性校验读取。`--help` 及不带 `--execute` 的前检通过：81 份源文件、`real_requests=0`。没有运行新全量模型评测，也没有接受旧候选。
