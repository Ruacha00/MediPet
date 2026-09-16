# MediPet 健康咨询与报告整理

U001 在原预约助手上增加症状初步分诊、药品说明书查询和报告整理。它提供有来源、可核对的信息，不替代医生诊断，不开处方，不评价其他医院或医生的治疗方案。医院目录仍是明和虚构医院；医学资料来自官方公开页面，两类来源不混用。

## 症状只给初步科室方向

[triage_symptoms](../health/triage.py) 根据用户原文和有限年龄信息提供初步方向。当前演示医院只有内科、儿科、眼科，规则仅覆盖部分呼吸道、腹部和眼部描述；不会为了回答未知症状而编造其他科室。年龄缺失或冲突、症状不明时返回待补信息，不能把初步方向当作疾病结论。

症状及就医边界参考 NHS 的[普通感冒](https://www.nhs.uk/conditions/common-cold/)、[结膜炎](https://www.nhs.uk/conditions/conjunctivitis/)、[儿童发热](https://www.nhs.uk/symptoms/fever-in-children/)和[腹痛](https://www.nhs.uk/symptoms/stomach-ache/)资料。映射到本项目三个科室是本地演示规则，不声称获得 NHS 临床验证。

[共享急症检测](../core/emergency.py) 在普通记忆与模型之前处理有限胸痛、呼吸困难、意识异常等表达及超过 39.5℃ 的演示中断条件，提示立即就医，中国大陆拨打 120。否定、过去经历和一般咨询须与当前症状区分。39.5℃ 来自本项目需求，不是所有年龄、人群统一适用的医学阈值；分诊服务还对小月龄发热、眼痛/视力变化等给出及时线下评估提示。

## 药品目录固定到两个美国标签

[药品服务](../health/medications.py) 和[静态目录](../health/data/medications.json)收录以下两项。可查询用途、标签用法用量、禁忌与警示、已收录的相互作用；卡片同时显示来源和核对日期。

| 查询示例与明确剂型 | 官方标签 | 范围 |
| --- | --- | --- |
| `对乙酰氨基酚500mg普通片` | [DailyMed，Preferred Pharmaceuticals，NDC 68788-4132](https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=983debdf-4a61-4dde-ba34-567fdd17720e&type=display) | 500 mg 普通口服片，标签版本 1，生效日期 2026-06-11 |
| `布洛芬200mg普通片` | [DailyMed，Dr. Reddy's，NDC 75907-428](https://dailymed.nlm.nih.gov/dailymed/fda/fdaDrugXsl.cfm?setid=4d830b9b-b396-9581-7572-c7f19ff17597&type=display) | 200 mg 普通包衣片，标签版本 1，生效日期 2026-07-01 |

以上是指定美国产品的公开标签摘要，不能替代用户手中不同地区、厂家或剂型的说明书。缓释、复方、其他规格、儿童液体剂型或含糊品牌不会自动匹配；模型工具参数也必须来自用户原话，不能替用户选药。

未知药物或未收录组合明确返回资料不足，不能据“未查到”判断安全。即使两个药分别已收录，也不代表它们之间的组合已核对。说明书数字属于标签信息，不能直接转换成个人剂量；个人适用性、儿童、孕哺期、肝肾异常和复杂合并用药需要医师或药师核对。当前函数没有完整个人用药审查所需的数据输入。

## 报告由本地程序提取和比较

在对话页先选择患者和事项，再使用“上传报告 PDF / 图片”。支持文字 PDF、扫描 PDF、PNG、JPEG、WebP；单文件最多 **10 MB**，PDF 为 **1–10 页**，图片为单帧且不超过 1600 万像素。也可在聊天中粘贴报告原文。

[上传处理](../health/report_upload.py) 对纯文字 PDF 直接提取，对扫描页、含图混合页和图片使用本地 Tesseract `chi_sim+eng`。混合页保留原文字层，另标出未校对的 OCR 补充。Docker 镜像包含中英文依赖。上传、OCR 和 [preprocess_report](../health/reports.py) 均不调用大模型，也不把文件发送到外部 OCR 服务；粘贴文字由聊天角色调用同一个确定性整理函数，聊天理解和后续回答仍可能调用模型。

程序只整理原文项目、数值、单位和明确的闭合参考范围，比较结果是低于、处于或高于该原文范围。缺少单位/范围、格式不支持或识别不可靠时保留原文并标为“待核对”；OCR 低置信度时整次结果不做区间判断。不会根据外部常识补数值、修正小数点或推断疾病。参考区间与诊断的区别见 [MedlinePlus 说明](https://medlineplus.gov/lab-tests/how-to-understand-your-lab-results/)。

损坏或加密 PDF、格式不符、超限、无文字、OCR 不可用或超时均返回可见错误。复杂排版、图片清晰度和 OCR 误识别仍会影响结果；请对照原件核对数值、单位和参考范围。支持上传格式不代表任意报告都能完整识别。

## 原件不存，报告内容进入当前事项

`POST /reports/preprocess` 先通过服务端校验患者与事项，原始文件仅在处理期间使用，不写入项目存储。上传文件名、提取文字、结构化 `report_summary` 卡片会保存到 Redis 的完整事项历史，并随历史恢复到页面。因此“原件不保留”不等于“医疗内容不保存”。

报告追问归 TriageAgent。它用 [read_current_report](../agents/tools.py) 从当前绑定患者、当前事项完整历史中取最新报告；没有记录时要求上传或粘贴，不跨患者或跨事项查找，不从摘要生成报告数值。若同一事项有多份报告，当前工具读取最新一份；需要区分多次就诊时应使用不同事项。普通情景记忆仍可能召回同患者其他事项的背景，但不能替代此报告事实入口。

## 知识、Skills 与验证状态

当前 **24 份知识文档、6 组 Skills、6 个角色**。新增的 7 份医疗知识涵盖常见症状 FAQ、两个标签与报告术语；新增 `health_triage` 和 `medication_information` Skill，全局服务边界常驻六角色。Skill 文件修改后须主动重载，规则才用于后续请求。

来源的 `reviewed_at` 表示本次资料核对日期，不是医学专家审查或自动更新保证。[来源及确定性验收](internal/updates/U001-health-consultation/evidence/medical-sources.md)记录分诊、药品、知识和 Skill 的 **97 项局部检查通过**；这不证明临床有效性、真实模型始终遵守、OCR 准确率或 RAG 效果。本轮全量684项通过、前端35项通过，真实组件/OCR/浏览器与模型验收已记录，见 [U001 检查点](internal/updates/U001-health-consultation/EXECUTION.md)。旧版 389 项后端回归及 54/12/12 真实评测属于医疗扩展前基准，不能移作本轮结论。

继续阅读：[演示脚本](demo-script.md) · [架构](architecture.md) · [工具与 RAG](tools-rag.md) · [记忆与 Skills](memory-skills.md)。
