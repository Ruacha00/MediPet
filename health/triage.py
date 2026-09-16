"""少量症状到演示医院现有科室的初步映射；不输出疾病结论。"""

import json
import re
from pathlib import Path

from core.emergency import detect_emergency
from health.models import RecommendedDepartment, SourceCitation, TriageGuidance


_NEGATIVE = re.compile(r"(?:没有|没|未|无|不是|并非|否认|不再|不)[^，。！？；]{0,8}$")
_REFERENCE = re.compile(r"什么意思|是什么意思|科普|举例|如果|假如|假设|以前|曾经")
_EYE = ("眼睛红", "眼睛发红", "眼红", "红眼", "眼睛痒", "眼睛发痒", "眼痒", "流泪", "眼睛干涩", "眼干", "眼屎")
_GENERAL = ("咳嗽", "流鼻涕", "鼻塞", "喉咙痛", "咽痛", "发烧", "发热", "腹痛", "肚子疼", "腹泻", "腹胀")


def _mentions(message: str, terms: tuple[str, ...]) -> list[str]:
    found = []
    for clause in re.split(r"[，,。！？!?；;\n]|但是|而是|但", message):
        if _REFERENCE.search(clause):
            continue
        for term in terms:
            for match in re.finditer(re.escape(term), clause):
                if not _NEGATIVE.search(clause[:match.start()]) and term not in found:
                    found.append(term)
    return found


def _age_in_months(message: str) -> int | None:
    """月数必须有年龄语境；咳嗽/发热持续几个月不构成月龄。"""
    ages = set()
    patterns = (
        r"(?:宝宝|婴儿|孩子|儿童)(?:现在|已经|刚满|今年|才|刚|满|是|有)?\s*(\d{1,3})\s*个月",
        r"(?:月龄|年龄)(?:为|是|[:：])?\s*(\d{1,3})\s*个月",
        r"(?<!\d)(\d{1,3})\s*个月(?:大|龄|的?婴儿|的?宝宝|的?孩子)",
    )
    for clause in re.split(r"[，,。！？!?；;\n]", message):
        if _REFERENCE.search(clause):
            continue
        for pattern in patterns:
            ages.update(int(match[1]) for match in re.finditer(pattern, clause))
    return next(iter(ages)) if len(ages) == 1 else None


def _age_group(message: str, value: str | None) -> str | None:
    normalized = (value or "").strip().casefold()
    if normalized in {"child", "children", "pediatric", "infant", "儿童", "孩子", "婴儿", "未成年"}:
        return "child"
    if normalized in {"adult", "older_adult", "成人", "成年人", "老人", "老年人"}:
        return "adult"
    if normalized:
        return None
    ages = re.findall(r"(?<!\d)(\d{1,3})\s*岁", message)
    if len(set(ages)) == 1:
        age = int(ages[0])
        return ("child" if age < 18 else "adult") if age <= 120 else None
    if len(set(ages)) > 1:
        return None
    months = _age_in_months(message)
    if _mentions(message, ("孩子", "儿童", "宝宝", "婴儿")) or (months is not None and months < 216):
        return "child"
    if _mentions(message, ("成人", "成年人", "老人")):
        return "adult"
    return None


def triage_symptoms(message: str, *, age_group: str | None = None) -> TriageGuidance:
    """资料不足先澄清；仅使用内科、儿科、眼科三个现有演示科室。"""
    text = message.strip() if isinstance(message, str) else ""
    source_data = json.loads((Path(__file__).with_name("data") / "sources.json").read_text(encoding="utf-8"))

    def sources(*keys: str) -> list[SourceCitation]:
        return [SourceCitation(**source_data[key]) for key in dict.fromkeys(keys)]

    # API/编排器也在入口使用同一共享急症检测，直接调用仍不能绕过。
    if detect_emergency(text):
        return TriageGuidance(title="优先获取紧急医疗帮助", summary="请立即就医或拨打当地急救电话；在中国大陆可拨打120。不要等待在线分诊或普通预约。这不是诊断。", recommended_departments=[], missing_information=[], sources=sources("nhs-child-fever", "nhs-stomach-ache"))

    # 下列来源要求尽快专业评估，不把眼痛/视力变化或严重腹痛排进普通预约。
    eye_urgent = _mentions(text, ("眼痛", "眼睛痛", "畏光", "视力下降", "视力模糊", "看不清", "突然失明"))
    abdomen_urgent = _mentions(text, ("剧烈腹痛", "肚子剧痛", "呕血", "黑便", "便血"))
    if eye_urgent or abdomen_urgent:
        keys = (["nhs-conjunctivitis"] if eye_urgent else []) + (["nhs-stomach-ache"] if abdomen_urgent else [])
        return TriageGuidance(title="需要尽快线下评估", summary="您描述的表现需要尽快由医护人员当面评估，不能仅凭普通科室建议排除急症。若突然或严重发作，请立即急诊就医或拨打120；不要等待演示挂号。", recommended_departments=[], missing_information=[], sources=sources(*keys))

    group = _age_group(text, age_group)
    fever = bool(_mentions(text, ("发烧", "发热")))
    temperatures = [float(match[1]) for match in re.finditer(r"(?<!\d)(\d{2}(?:\.\d+)?)\s*(?:℃|°C|度)", text, re.I)
                    if _mentions(text, (match[0],))]
    if group == "child" and (fever or temperatures):
        months = _age_in_months(text)
        urgent = months is not None and ((months < 3 and (fever or any(value >= 38 for value in temperatures))) or
                                        (3 <= months <= 6 and (any(value >= 39 for value in temperatures) or (fever and not temperatures))))
        if urgent:
            return TriageGuidance(title="婴儿发热需及时就医", summary="小月龄婴儿发热不能等待普通在线分诊：不足3个月有发热表现，或3至6个月体温达到39℃及以上，应及时联系医生评估。不要根据成人药品标签自行给药。", recommended_departments=[], missing_information=[], sources=sources("nhs-child-fever"))

    eye = _mentions(text, _EYE)
    general = _mentions(text, _GENERAL)
    departments = []
    missing = []
    citations = []
    if eye:
        departments.append(RecommendedDepartment(department_id="dep_ophthalmology", name="眼科", reason="您提到眼部不适；可先向演示医院眼科咨询检查安排，不能据此诊断为结膜炎或其他疾病。"))
        citations.append("nhs-conjunctivitis")
    if general:
        citations.append("nhs-stomach-ache" if any(word in general for word in ("腹痛", "肚子疼", "腹泻", "腹胀")) else "nhs-common-cold")
        if group is None:
            missing.append("请补充就诊人的年龄或年龄段，以便区分成人内科与儿科；不根据账号身份猜测年龄。")
        elif group == "child":
            departments.append(RecommendedDepartment(department_id="dep_pediatrics", name="儿科", reason="根据您明确的儿童年龄信息和症状，可先向演示医院儿科咨询；这不是病因判断。"))
            citations.append("nhs-child-fever")
        else:
            departments.append(RecommendedDepartment(department_id="dep_internal", name="内科", reason="针对成人的一般呼吸道或腹部不适，可先向演示医院内科咨询；最终科室由现场导诊或医生确认。"))
    if not eye and not general:
        missing.append("请说明主要症状、部位、年龄、持续多久及是否突然加重；当前有限规则尚不能给出合适科室，请联系医院导诊。")
    elif not re.search(r"\d+\s*(?:天|小时|周|个月)|今天|刚刚|今早|昨天", text):
        missing.append("请补充症状持续时间、严重程度及是否伴随呼吸困难、意识异常或突然加重。")
    return TriageGuidance(
        title="初步就诊科室建议" if departments else "需要补充症状信息",
        summary="以下只是在明和虚构医院现有科室内的初步就诊方向，不是诊断，不替代医生。资料不足或症状变化时应由现场导诊确认；未命中急症规则不代表安全。",
        recommended_departments=departments, missing_information=missing, sources=sources(*citations),
    )
