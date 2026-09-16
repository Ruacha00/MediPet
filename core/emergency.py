"""固定中文演示语句的急症入口规则；不进行诊断或症状推理。"""

import re


EMERGENCY_SIGNALS = ("现在呼吸困难", "突然失去意识", "现在剧烈胸痛")
EMERGENCY_RESPONSE = (
    "当前消息命中预设的急症演示信号。请立即联系现场医护人员或当地急救服务，"
    "不要等待在线聊天回复。MediPet 不提供急症诊断，也不会在此时办理预约。"
)

_REFERENCE = re.compile(r"什么意思|什么含义|是什么意思|是指什么|这种信号|这种症状|如何(?:理解|预防|识别)|怎么(?:理解|判断)|科普|举例")
_CONDITIONAL_OR_PAST = re.compile(r"如果|假如|假设|万一|曾经|昨天|以前|之前|过去")
_NEGATED = re.compile(r"(?:没有|没|未|无|不是|并非|否认|不曾)[^，,。！？!?；;]{0,10}$")


def detect_emergency(message: str) -> bool:
    """只判断固定信号的当前肯定陈述；逐分句排除否定、假设与知识询问。"""
    if not isinstance(message, str):
        return False
    for clause in re.split(r"[，,。！？!?；;\n]", message):
        if _REFERENCE.search(clause):
            continue
        for signal in EMERGENCY_SIGNALS:
            for match in re.finditer(re.escape(signal), clause):
                prefix, suffix = clause[:match.start()], clause[match.end():]
                if _NEGATED.search(prefix) or _CONDITIONAL_OR_PAST.search(prefix):
                    continue
                if suffix.strip().startswith(("吗", "是否")):
                    continue
                return True
    return False


def is_emergency_reference(message: str) -> bool:
    """含固定信号但没有当前肯定命中，供普通分类结果同步排除。"""
    return isinstance(message, str) and any(signal in message for signal in EMERGENCY_SIGNALS) and not detect_emergency(message)
