"""Report transcription and explicit reference-range comparison, without diagnosis."""
import re
from decimal import Decimal, InvalidOperation

from health.models import ReportObservation, ReportSummary

MAX_REPORT_TEXT = 100_000
_NUMBER = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
_ROW = re.compile(
    rf"^(?P<item>.+?)\s+(?P<value>{_NUMBER})\s*[↑↓*]?\s+"
    rf"(?P<unit>[^\s|]+)\s+(?:参考(?:范围|区间)\s*[:：]?\s*)?"
    rf"(?P<low>{_NUMBER})\s*[-~～—–至]\s*(?P<high>{_NUMBER})\s*$"
)
_PARTIAL = re.compile(r"^(?P<item>.+?)\s+(?P<value>\S+)(?:\s+(?P<rest>.*))?$")


def preprocess_report(text: str, *, input_kind="text") -> ReportSummary:
    """Compare only a numeric result with its own explicit unit and closed range."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("报告文字不能为空。")
    if len(text) > MAX_REPORT_TEXT:
        raise ValueError("提取文字超过 100000 字符，请拆分报告。")
    if input_kind not in {"text", "pdf", "image"}:
        raise ValueError("不支持的报告输入类型。")
    observations = []
    unresolved = False
    for raw_line in text.splitlines():
        line = re.sub(r"[\t|]+", " ", raw_line).strip()
        if not line or not re.search(r"\d", line):
            continue
        match = _ROW.fullmatch(line)
        if match and "�" not in line:
            item, value, unit, low, high = (match[name] for name in ("item", "value", "unit", "low", "high"))
            # A range in the unit column, date, or numeric 'unit' is not a laboratory row.
            valid_unit = bool(re.fullmatch(r"[%％]|[A-Za-zμµ]+/[A-Za-zμµ]+|[x×]?10(?:\^\d+|[⁰¹²³⁴⁵⁶⁷⁸⁹]+)/(?:[uμµm]?L)|fL|pg|s|秒|mmHg|kPa|次/分|个/[uμµ]?L", unit))
            try:
                number, lower, upper = Decimal(value), Decimal(low), Decimal(high)
                valid_range = lower <= upper
            except InvalidOperation:
                valid_range = False
            flag = "unassessed"
            if valid_unit and valid_range:
                flag = "below" if number < lower else "above" if number > upper else "within"
            unresolved |= flag == "unassessed"
            observations.append(ReportObservation(item=item, value=value, unit=unit,
                reference_range=f"{low}–{high}", flag=flag, raw_line=raw_line))
        else:
            # Retain unfamiliar layouts verbatim. Do not repair OCR O/0 or invent units.
            partial = _PARTIAL.fullmatch(line)
            observations.append(ReportObservation(item=partial["item"] if partial else "待核对原文",
                value=partial["value"] if partial else line, flag="unassessed", raw_line=raw_line))
            unresolved = True
    warnings = ["仅按报告原文的数值、单位和参考区间整理；超出区间不等同于疾病诊断，请结合医生解释。"]
    if input_kind != "text":
        warnings.append("文件提取可能存在识别或排版错误，请逐项对照原报告，尤其核对小数点、单位和参考区间。")
    if unresolved or not observations:
        warnings.append("部分内容缺少明确数值、单位或闭合参考区间，或排版无法可靠识别，已保留原文并标为待核对。")
    return ReportSummary(title="报告文字整理", summary=f"整理出 {len(observations)} 项待核对记录；不推断疾病或治疗方案。",
        input_kind=input_kind, extracted_text=text, observations=observations, warnings=warnings, sources=[])
