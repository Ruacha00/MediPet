from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

MANUAL_TRIAGE_GUIDANCE = (
    "我不能诊断，也不能根据症状判断或推荐科室，请联系服务医院人工导诊。"
)


class DepartmentBoundaryResult(StrEnum):
    MANUAL_TRIAGE_REQUIRED = "MANUAL_TRIAGE_REQUIRED"
    EXPLICIT_TARGET_ALLOWED = "EXPLICIT_TARGET_ALLOWED"
    AGENT_ALLOWED = "AGENT_ALLOWED"


class DepartmentBoundaryReason(StrEnum):
    CURRENT_SYMPTOM_ROUTING_REQUEST = "CURRENT_SYMPTOM_ROUTING_REQUEST"
    FOLLOWUP_SYMPTOM_ROUTING_REQUEST = "FOLLOWUP_SYMPTOM_ROUTING_REQUEST"
    CURRENT_SLOT_SELECTION = "CURRENT_SLOT_SELECTION"
    CURRENT_EXPLICIT_TARGET = "CURRENT_EXPLICIT_TARGET"
    ORDINARY_ASSISTANCE = "ORDINARY_ASSISTANCE"


@dataclass(frozen=True)
class DepartmentBoundaryDecision:
    result: DepartmentBoundaryResult
    reason: DepartmentBoundaryReason
    response: str | None = None

    @property
    def allows_agent(self) -> bool:
        return self.result is not DepartmentBoundaryResult.MANUAL_TRIAGE_REQUIRED

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
    r"(?:(?:挂|看|去|就诊|选择|推荐).{0,6}(?:哪|哪个|哪一|什么).{0,3}(?:科|科室)|"
    r"(?:哪|哪个|哪一|什么).{0,3}(?:科|科室).{0,3}(?:合适|适合|好))"
)
_DEPARTMENT_COMPARISON = re.compile(r"(?:哪个|哪一).{0,4}(?:更)?(?:合适|适合)")
_DEPARTMENT_SUITABILITY = re.compile(r"(?:科|科室).{0,5}(?:合适|适合|好)")
_SYMPTOM_DESTINATION = re.compile(r"(?:应该|需要|可以).{0,3}(?:去|到).{0,3}(?:哪里|哪儿)")
_NAMED_DEPARTMENT = re.compile(r"[\u4e00-\u9fff]{1,12}科(?:室|门诊)?")
_SEMANTIC_FRAGMENT_BOUNDARY = re.compile(r"(?:但是|不过|然而|但)|[，。！？；,.!?;]")

_EXPLICIT_TARGET_ACTIONS = (
    "医生",
    "号源",
    "挂号",
    "预约",
    "在哪里",
    "位置",
    "怎么走",
    "前往",
    "想去",
    "要去",
    "已决定",
    "已经决定",
    "选定",
    "选择",
)

_SERVICE_LOCATION_MARKERS = (
    "门诊检验处",
    "检验处",
    "影像科",
    "药房",
    "收费处",
    "服务台",
)

_ROUTING_NEGATION_MARKERS = (
    "不是在问",
    "不想问",
    "无需问",
    "不用问",
)

_ROUTING_EDUCATION_MARKERS = (
    "如果有人",
    "假如有人",
    "假设有人",
    "科普",
)

_HISTORICAL_CONTEXT_MARKERS = ("昨天", "以前", "曾经", "当时", "病史")
_RESOLVED_CONTEXT_MARKERS = ("已经好了", "已恢复", "已经缓解", "已缓解")


def evaluate_department_boundary(
    *,
    current_message: str,
    previous_participant_message: str | None = None,
    has_selected_slot: bool = False,
) -> DepartmentBoundaryDecision:
    compact = "".join(current_message.split())
    if _is_current_symptom_routing_request(compact):
        return DepartmentBoundaryDecision(
            result=DepartmentBoundaryResult.MANUAL_TRIAGE_REQUIRED,
            reason=DepartmentBoundaryReason.CURRENT_SYMPTOM_ROUTING_REQUEST,
            response=MANUAL_TRIAGE_GUIDANCE,
        )
    previous_compact = (
        "".join(previous_participant_message.split())
        if previous_participant_message is not None
        else ""
    )
    if _is_routing_followup(compact) and _has_current_symptom_context(previous_compact):
        return DepartmentBoundaryDecision(
            result=DepartmentBoundaryResult.MANUAL_TRIAGE_REQUIRED,
            reason=DepartmentBoundaryReason.FOLLOWUP_SYMPTOM_ROUTING_REQUEST,
            response=MANUAL_TRIAGE_GUIDANCE,
        )
    if has_selected_slot:
        return DepartmentBoundaryDecision(
            result=DepartmentBoundaryResult.EXPLICIT_TARGET_ALLOWED,
            reason=DepartmentBoundaryReason.CURRENT_SLOT_SELECTION,
        )
    if _has_explicit_target(compact):
        return DepartmentBoundaryDecision(
            result=DepartmentBoundaryResult.EXPLICIT_TARGET_ALLOWED,
            reason=DepartmentBoundaryReason.CURRENT_EXPLICIT_TARGET,
        )
    return DepartmentBoundaryDecision(
        result=DepartmentBoundaryResult.AGENT_ALLOWED,
        reason=DepartmentBoundaryReason.ORDINARY_ASSISTANCE,
    )


def _is_current_symptom_routing_request(compact: str) -> bool:
    if not _has_current_symptom_context(compact):
        return False
    return _is_routing_followup(compact) or (
        "预约检查" in compact and "科" not in compact
    )


def _has_current_symptom_context(compact: str) -> bool:
    if not compact or _has_excluded_routing_context(compact):
        return False
    fragments = [
        fragment
        for fragment in _SEMANTIC_FRAGMENT_BOUNDARY.split(compact)
        if fragment
    ]
    for index, fragment in enumerate(fragments):
        if not any(marker in fragment for marker in _SYMPTOM_MARKERS):
            continue
        if _is_resolved_historical_symptom(fragments, index):
            continue
        return True
    return False


def _is_resolved_historical_symptom(fragments: list[str], index: int) -> bool:
    fragment = fragments[index]
    if not any(marker in fragment for marker in _HISTORICAL_CONTEXT_MARKERS):
        return False
    for candidate_index, candidate in enumerate(fragments[index:], start=index):
        if candidate_index > index and any(
            marker in candidate for marker in _SYMPTOM_MARKERS
        ):
            break
        if any(marker in candidate for marker in _RESOLVED_CONTEXT_MARKERS):
            return True
    return False


def _is_routing_followup(compact: str) -> bool:
    if _DEPARTMENT_REQUEST.search(compact):
        return True
    if "科" in compact and _DEPARTMENT_COMPARISON.search(compact):
        return True
    if _DEPARTMENT_SUITABILITY.search(compact):
        return True
    if compact.count("科") >= 2 and "还是" in compact:
        return True
    return bool(_SYMPTOM_DESTINATION.search(compact))


def _has_excluded_routing_context(compact: str) -> bool:
    if any(marker in compact for marker in _ROUTING_NEGATION_MARKERS):
        return True
    return any(marker in compact for marker in _ROUTING_EDUCATION_MARKERS)


def _has_explicit_target(compact: str) -> bool:
    if not any(action in compact for action in _EXPLICIT_TARGET_ACTIONS):
        return False
    if any(location in compact for location in _SERVICE_LOCATION_MARKERS):
        return True
    return any(
        not match.group().endswith(("哪科", "什么科", "哪些科", "各科"))
        for match in _NAMED_DEPARTMENT.finditer(compact)
    )
