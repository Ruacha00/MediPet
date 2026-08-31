from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, TypedDict


class EmergencyHandoffData(TypedDict):
    priority: Literal["emergency"]
    title: str
    description: str


class EmergencyHandoffPart(TypedDict):
    type: Literal["data-handoff"]
    data: EmergencyHandoffData


@dataclass(frozen=True)
class EmergencyHandoff:
    title: str
    description: str

    def message_part(self) -> EmergencyHandoffPart:
        data: EmergencyHandoffData = {
            "priority": "emergency",
            "title": self.title,
            "description": self.description,
        }
        part: EmergencyHandoffPart = {
            "type": "data-handoff",
            "data": data,
        }
        return part


@dataclass(frozen=True)
class EmergencySignal:
    phrase: str
    requires_current_context: bool = False


_EMERGENCY_HANDOFF = EmergencyHandoff(
    title="请立即寻求线下急救",
    description=(
        "请立即拨打 120 或前往最近的医院急诊；如身边有人，请让其陪同或"
        "协助呼救。不要等待 MediPet 的后续回复。"
    ),
)

_SIGNALS = (
    EmergencySignal("昏迷"),
    EmergencySignal("叫不醒"),
    EmergencySignal("失去意识"),
    EmergencySignal("意识不清"),
    EmergencySignal("胸痛", requires_current_context=True),
    EmergencySignal("胸口剧痛"),
    EmergencySignal("呼吸困难", requires_current_context=True),
    EmergencySignal("喘不上气"),
    EmergencySignal("无法呼吸"),
    EmergencySignal("不能呼吸"),
    EmergencySignal("呼吸停止"),
    EmergencySignal("快晕倒"),
    EmergencySignal("抽搐", requires_current_context=True),
    EmergencySignal("大量出血"),
    EmergencySignal("大出血"),
    EmergencySignal("血止不住"),
    EmergencySignal("误服农药"),
    EmergencySignal("煤气中毒"),
    EmergencySignal("一氧化碳中毒"),
    EmergencySignal("突然口角歪斜"),
    EmergencySignal("一侧手脚无力"),
    EmergencySignal("突然说话不清"),
)

_NEGATION_PATTERN = re.compile(
    r"(?:不存在|不是|并非|并不|并没有|没有|没|未|无|否认|不)"
    r"(?:存在|再|曾|出现|发生|感到|感觉到|觉得|感觉|表现为|任何|很|太|十分|"
    r"特别|比较|完全|明显|持续|有|过|见|的)*$"
)

_EDUCATION_MARKERS = (
    "我想了解",
    "为什么",
    "如果有人",
    "请问如果",
    "假如有人",
    "假设有人",
    "若有人",
    "请介绍",
    "是什么意思",
    "什么是",
    "如何识别",
    "怎么识别",
    "急救知识",
    "急救常识",
    "急救培训",
    "培训中",
    "教学",
    "学员",
    "案例",
    "科普",
)

_PARTICIPANT_SUBJECT_MARKERS = (
    "我爸",
    "我妈",
    "孩子",
    "家人",
    "老人",
    "本人",
    "患者",
    "他",
    "她",
    "我",
)

_CURRENT_STATE_MARKERS = (
    "现在",
    "正在",
    "刚刚",
    "刚才",
    "目前",
    "此刻",
    "突然",
    "持续",
    "越来越",
)

_CURRENT_MARKERS = (*_CURRENT_STATE_MARKERS, "今天")

_CURRENT_AFTER_SIGNAL_MARKERS = (
    "现在就这样",
    "现在就是这样",
    "目前就这样",
    "目前就是这样",
    "我爸就是这样",
    "我妈就是这样",
    "他就是这样",
    "她就是这样",
    "孩子就是这样",
    "家人就是这样",
    "老人就是这样",
    "本人就是这样",
    "越来越",
    "还在",
    "还没好",
    "仍没好",
    "仍未好转",
    "未缓解",
    "没有缓解",
    "持续",
)

_NON_CURRENT_MARKERS = (
    "最近",
    "偶尔",
    "上周",
    "上个月",
    "昨天",
    "前天",
    "以前",
    "曾经",
    "之前",
    "过去",
    "有过",
    "去年",
    "小时候",
    "当时",
    "几天前",
    "一周前",
    "一个月前",
)

_HISTORICAL_AFTER_SIGNAL_MARKERS = (
    "病史",
    "发作史",
)

_RESOLUTION_MARKERS = (
    "已经好了",
    "已经恢复",
    "已恢复",
    "已经缓解",
    "已缓解",
    "已经结束",
    "已结束",
)


def emergency_interruption_for(message: str) -> EmergencyHandoff | None:
    compact = "".join(message.split())
    for signal in _SIGNALS:
        phrase = signal.phrase
        start = 0
        while (index := compact.find(phrase, start)) >= 0:
            prefix = compact[max(0, index - 12) : index]
            has_current_context = _has_current_context(compact, index, phrase)
            if (
                not _is_negated(prefix)
                and not _is_non_current(compact, index, phrase)
                and not _is_educational(compact, index, phrase)
                and (
                    not signal.requires_current_context or has_current_context
                )
            ):
                return _EMERGENCY_HANDOFF
            start = index + len(phrase)
    return None


def _is_educational(message: str, index: int, phrase: str) -> bool:
    latest_education_end = max(
        (
            position + len(marker)
            for marker in _EDUCATION_MARKERS
            if (position := message.rfind(marker, 0, index)) >= 0
        ),
        default=-1,
    )
    if latest_education_end < 0:
        return False
    prefix = message[max(0, index - 8) : index]
    has_current_subject = any(
        marker in message[latest_education_end:index]
        for marker in _PARTICIPANT_SUBJECT_MARKERS
    )
    return not (
        _has_current_state_context(message, index, phrase)
        or ("今天" in prefix and has_current_subject)
    )


def _is_negated(prefix: str) -> bool:
    return _NEGATION_PATTERN.search(prefix) is not None


def _has_current_context(message: str, index: int, phrase: str) -> bool:
    prefix = message[max(0, index - 8) : index]
    return (
        _has_current_state_context(message, index, phrase)
        or "今天" in prefix
    )


def _has_current_state_context(message: str, index: int, phrase: str) -> bool:
    prefix = message[max(0, index - 8) : index]
    suffix = message[index + len(phrase) : index + len(phrase) + 20]
    return (
        suffix.startswith(("了", "着"))
        or any(marker in prefix for marker in _CURRENT_STATE_MARKERS)
        or any(marker in suffix for marker in _CURRENT_AFTER_SIGNAL_MARKERS)
    )


def _is_non_current(
    message: str,
    index: int,
    phrase: str,
) -> bool:
    prefix = message[:index]
    suffix = message[index + len(phrase) : index + len(phrase) + 12]
    if suffix.startswith(_HISTORICAL_AFTER_SIGNAL_MARKERS) or any(
        marker in suffix for marker in _RESOLUTION_MARKERS
    ):
        return True
    if any(marker in suffix for marker in _CURRENT_AFTER_SIGNAL_MARKERS):
        return False
    latest_non_current = max(
        (prefix.rfind(marker) for marker in _NON_CURRENT_MARKERS),
        default=-1,
    )
    latest_current = max(
        (prefix.rfind(marker) for marker in _CURRENT_MARKERS),
        default=-1,
    )
    return latest_non_current > latest_current
