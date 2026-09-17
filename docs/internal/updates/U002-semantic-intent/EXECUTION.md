# U002 执行检查点

更新：2026-09-17。本文件由root维护。恢复先读AGENTS、IMPLEMENTATION_PLAN、本文、计划和相应issue。

## 当前结论

用户授权并行实施；01—07 done，08已执行冻结对照但**候选未通过，保持in_progress**，09 todo，10按最新用户要求先行更新学习文档与简历，保持in_progress。唯一未通过的预声明效果门槛：LLM失败接受准确率69/84=82.143%，要求90%；覆盖84/160=52.5%达标。禁止将本轮标为整体完成或用同一holdout继续调参。

[真实结果与恢复记录](evidence/calibration/implementation.md)、[机器判定](evidence/calibration/gate-result.json)、[冻结配置](../../../../config/intent_embedding_calibration.json)。原66语义/哈希66/66、disabled65/66；留出三模式融合185/186持平。单路semantic95/160、hash62/160；低重合80/132与52/132。正式样本总252条，238次真实DeepSeek请求，失败0；固定急症跳过模型。

已向用户发异步路线选择：优先补场景表达模板并保留轻量模型（推荐），或比较更大中文模型并接受资源开销。目前尚无回答；这不是用户已批准新一轮模型替换。下一轮需先明确改进范围、开发规则和新的独立留出样本，保留本次全部失败证据。原V06/U001候选未被自动接受。

## 工作树与边界

2026-09-17交付进展：当前候选已提交为`e81f447`并成功推送`origin/codex/u002-semantic-intent`。提交前刷新本树索引为4490节点/10346边/299流程；5批完整变更分析覆盖116路径，无error/partial/truncated，分析期间文件未变化。秘密扫描未命中真实密钥；71份新增证据/数据的暂存字节与工作文件一致。枚举注释与历史源码指纹差异见[提交说明](evidence/change-analysis/checkpoint-notes.md)。推送后开始“文档+简历”增量修订，后续文档修改与该实现提交分开。

文档阶段已完成：原9篇教程/简历加文档中心共10篇，保留全部95个H1/H2及原教学顺序，补语义编码/哈希后备、模板与few-shot区别、learn边界、部署、正式指标与面试口径。独立复核后明确`EvalReport`接受接口不能直接接收U002专项JSON。最终141个本地链接有效，168个代码块边界完整，Python片段去除公共缩进后及JSON语法检查通过，`git diff --check`通过；方法与结果保存在`.scratch/U002-validate-docs.py`、`.scratch/U002-docs-validation.json`。没有新增模型请求或改变运行代码，不为纯文档重复运行已通过的业务测试。

后续文档修改保留未提交，远端仍为上述实现快照。提交后索引增量刷新出现全文索引问题，局部修复未成功，随后完整`--force --index-only`重建成功（4499节点/10367边/299流程）；日志`.scratch/U002-docs-final-index.log`。全文检索修复不改变业务代码或提交前已完成的检查。当前两个文档子agent均已交回。

- `D:/Projects/Agent Learn/Project/MediPet-Rebuild`，分支`codex/u002-semantic-intent`，起点`7a4e22a23d33a0bfa910f19662f2a8e40e429d54`。
- 用户于2026-09-17授权先提交并推送当前候选版本，再增量更新“文档+简历”。本次保存不代表08效果门槛通过，不接受评测基线、不合并或变更默认分支。文档更新可按此最新要求先行，09仍待验收。范围外未跟踪`docs/internal/rebuild/evidence/document-source-manifest.json`保留。
- 原源码、旧工作树、stash、.env保留；模型/构建环境/缓存ignored。Docker用户重置的归因单独见下文，不把卷存在解释为数据未变。

## 已完成交付

- 02：BGE small zh v1.5，revision `7999e1d3359715c523056ef9478215996d62a620`，FP32 ONNX、512维、CLS+L2、512tokens、无前缀。22条参考最小cos .9999999701；32项含真实ONNX测试通过，生产无torch/transformers。
- 03/04：三入口共享编码器、实例模板/learn隔离、同空间整批快照、完整输入版本缓存、API/评测诊断；校准分数/分差与LLM故障投票。独立审查修复向量先返回后等待LLM期间空间/learn变化的旧快照投票，并保持disabled缓存。
- 05：13项本地含真实模型测试、Linux容器非root/只读/断网真实推理通过，2CPU/2GB；资产在/opt，不被api-data遮蔽。首次apt/pip仍需联网，完整断网build失败如实记录。
- 06：独立审阅dev114/holdout186，主96/160，holdout低重合132/14类。8项结构检查通过；推理前修订8条跨split家族表达后冻结。当前holdout已使用，不能再当未见数据。
- 07：排名/真实响应采集/三模式回放/最多25组dev网格；生产recognize、校准与_vote复用。19项合成测试和1项并发中断定向通过。请求前fsync started，completed落盘；uncertain阻止重发。quality_valid防止降级冒充语义，fusion_quality_valid另要求真实且无LLM失败。
- 08：先预声明网格各25组，dev选择semantic .55/.04、hash .15/.03；关键词.75，正常权重不变。独立复核选择/指纹通过。100次同句暖查询semantic P50/P95 8.82/9.29ms（并发1/线程2，含模板比较，不含LLM）；整进程峰值238.71MiB。正式留出和原66已完成，未达标如上。

## 验证与静态检查

完整非integration后端752 passed/24 skipped（93.40秒）；在disabled时序修复后101项识别器通过；冻结配置后249项识别器/急症/API/评测API通过。所有结果在evidence/wiring，不能相加冒充独立测试总量。02/05真实证据另在embedding/runtime；07替身测试不作真实模型质量证据。

GitNexus明确本树index-only刷新：4476节点/10332边/299流程。临时索引5批覆盖111变更路径，无error/partial/truncated，真实暂存区不变，输入分析期间无变化；evidence/change-analysis保存原始输出。图本身有73入口候选/611callee省略及15预算截断，已结合源码/定向测试核实三入口与核心调用，不能声称静态图穷尽动态路径。随后114候选文件秘密扫描无真实密钥命中；首次命中两处既有公共Redis示例密码已核对HEAD并单独记录。分析后仅补汇总/状态。

## 子agent与文件交回

本次文档阶段复用既有两个子agent，root负责文档中心和状态，作者与复核者分工，总并发root+2；未递归派生或并发改同一文件。
- spec_agents_knowledge：02、06独立复核、识别器审查、07审查、dev选择复核；本轮只读复核教程。5篇核心文档补丁草稿`.scratch/U002-docs-core.patch`仍未应用，不把草稿当交付。
- audit_preparation_scope：06/07；本轮已应用9篇原教程补丁，并在原评测/面试章节补正式结果与限制。
- spec_api_ui：05；09仅准备隔离存储/浏览器脚本，没有正式API/前端启动或真实业务模型请求。

教程保留原章节/练习，不写未获得的效果通过数字。09未开始、10仍有最终验收与交付依赖，不能因先行文档修订就关闭整个issue。下一次调度使用followup，先交代新一轮文件/数据所有权。

## Docker、环境与进程

Docker最初Inference/Secrets socket失败。agent只将普通run临时目录备份重命名并隐藏启动Desktop；日志12:47:48的factory reset由用户明确确认亲自点击。12:48:07引擎恢复，详见evidence/runtime/docker-recovery.md。对象可枚举不证明原数据内容未变。

独立项目medipet-u002-validation仅Redis26379/Chroma28001 healthy，两个新卷；API28000/前端28088未启动，原8服务仍停止。没有删除、重置或挂载原卷。09后续信息见evidence/runtime/u002-09-preparation.md。

当前没有活跃root测试、下载、导出、索引或付费模型进程。主要session：94421开发退出0；38464正式首轮在额外旧提交比较急症空响应处退出1；13848仅修比较脚本、复用已有holdout产物、不重发，再首次执行66回归，退出0。52307索引与变更分析已退出0。原始报告/started+completed日志均在evidence/calibration，禁止为“补结果”重新收费采集；恢复必须先核对指纹与checkpoint。
