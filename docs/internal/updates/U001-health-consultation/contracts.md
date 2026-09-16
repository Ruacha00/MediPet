# U001 共享契约

root 维护。新增字段和类型先同步此文件及消费者。

## 意图和角色

- SYMPTOM_QUERY → TRIAGE / TriageAgent。
- MEDICATION_QUERY → MEDICATION / MedicationAgent。
- REPORT_QUERY → TRIAGE / TriageAgent；不新增职责重复的 ReportAgent。
- APPOINTMENT、EMERGENCY 和原细分事务意图保留。
- Triage 允许 triage_symptoms、preprocess_report、read_current_report、search_knowledge_base；Medication 允许 medication_information、search_knowledge_base。
- read_current_report 先校验请求绑定患者和事项，只读取同事项最新 report_summary 卡片，用于上传/粘贴后的追问；不跨患者或其他事项回退，不从压缩摘要重建数值。
- 原投票、主辅并行、最多三次模型请求/次工具循环保持。源数据读取走服务；最终预约仍须现有确认接口。

## 数据模型

health/models.py 中使用 Pydantic extra=forbid，不依赖 hospital.models，避免循环导入。以下 list 字段默认空列表。

- SourceCitation：source_id、title、url、reviewed_at（字符串）。
- GuidanceSection：heading、items（字符串列表）。
- RecommendedDepartment：department_id、name、reason。
- TriageGuidance：title、summary、recommended_departments、missing_information（字符串列表）、sources。
- MedicationInfo：title、summary、drug_name、formulation、sections、sources。
- ReportObservation：item、value、unit（默认空）、reference_range（默认空）、flag（unassessed/below/within/above，默认 unassessed）、raw_line。
- ReportSummary：title、summary、input_kind（text/pdf/image，默认 text）、extracted_text、observations、warnings（字符串列表）、sources。

hospital.models.Artifact 新增 triage_guidance、medication_info、report_summary 并严格校验对应模型。卡片与来源均由工具/预处理结果生成，通过现有历史持久化；模型只整理语言。

## 服务函数

- health.triage.triage_symptoms(message: str, *, age_group: str | None = None) → TriageGuidance。
- health.medications.medication_information(drug_name: str, *, other_drugs: list[str] | None = None) → MedicationInfo。
- health.reports.preprocess_report(text: str, *, input_kind='text') → ReportSummary。
- 普通未知药品/症状用明确的待补资料或未收录内容表示，不能编造；输入类型不合规则按边界返回错误。

## 报告上传

POST /reports/preprocess，multipart：file、user_id、patient_id、conv_id。服务器复用事项身份加载及归档校验。接受文字 PDF、扫描 PDF、PNG/JPEG/WebP 图片；最大 10 MB、PDF 最多 10 页，并限制像素与 OCR 执行时间。

使用本地提取/OCR。返回 patient_id、conv_id、visit、response、artifacts；提取原文在 report_summary.extracted_text，新增同事项用户/助手 chat 消息。保留当前预约方案及选择，不新增持久文件目录。前端接收原始提取结果供核对，不把 OCR 结果当成已经校正的事实。

上传失败使用已有 HTTP 错误展示约定，明确区分不支持、超限、损坏/加密和识别失败。报告只比较来源同一行明确提供的数值与参考范围；缺范围或提取不清的字段保持 unassessed。原件不持久保存，合成验证文件放测试/证据约定位置。
