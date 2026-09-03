from __future__ import annotations

import re

MANUAL_TRIAGE_GUIDANCE = (
    "我不能根据症状判断或推荐科室，请联系服务医院人工导诊。"
)

_SYMPTOM_MARKERS = (
    "发烧",
    "发热",
    "疼",
    "痛",
    "呼吸困难",
    "喘",
    "咳",
    "呕吐",
    "腹泻",
    "头晕",
    "眩晕",
    "皮疹",
    "出血",
    "抽搐",
    "昏迷",
    "意识不清",
    "不舒服",
    "不适",
)

_DEPARTMENT_REQUEST = re.compile(
    r"(?:挂|看|就诊|选择|推荐).{0,6}(?:哪个|哪一|什么).{0,3}(?:科|科室)"
)
_DEPARTMENT_COMPARISON = re.compile(r"(?:哪个|哪一).{0,4}(?:更)?(?:合适|适合)")
_SYMPTOM_DESTINATION = re.compile(r"(?:应该|需要|可以).{0,3}(?:去|到).{0,3}(?:哪里|哪儿)")


def manual_triage_guidance_for(message: str) -> str | None:
    compact = "".join(message.split())
    if not any(marker in compact for marker in _SYMPTOM_MARKERS):
        return None
    if _DEPARTMENT_REQUEST.search(compact):
        return MANUAL_TRIAGE_GUIDANCE
    if "科" in compact and _DEPARTMENT_COMPARISON.search(compact):
        return MANUAL_TRIAGE_GUIDANCE
    if compact.count("科") >= 2 and "还是" in compact:
        return MANUAL_TRIAGE_GUIDANCE
    if _SYMPTOM_DESTINATION.search(compact):
        return MANUAL_TRIAGE_GUIDANCE
    if "预约检查" in compact and "科" not in compact:
        return MANUAL_TRIAGE_GUIDANCE
    return None
