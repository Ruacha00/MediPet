"""有界中文急症入口规则；只触发固定求助提示，不进行诊断。"""

import re
import unicodedata


# 供分类器展示/兼容使用；是否触发以 detect_emergency 的语境判断为准。
EMERGENCY_SIGNALS = (
    "胸痛", "胸口痛", "胸口疼", "胸口疼痛", "胸部疼痛", "胸口剧烈疼痛",
    "呼吸困难", "喘不过气", "喘不上气", "无法呼吸",
    "失去意识", "意识丧失", "昏迷", "不省人事", "叫不醒",
)
EMERGENCY_RESPONSE = (
    "您描述的情况触发了急症求助提示。请立即寻求现场医护帮助；"
    "在中国请拨打急救电话 120，在境外请拨打当地急救电话。"
    "不要等待在线聊天回复。MediPet 不能替代医生诊断，也不会在此时办理预约。"
)

_SYMPTOM = re.compile("|".join(sorted(map(re.escape, EMERGENCY_SIGNALS), key=len, reverse=True)))
_REFERENCE = re.compile(
    r"什么意思|什么含义|是指什么|什么叫|什么是|是什么$|这种信号|这种症状|"
    r"如何(?:理解|预防|识别)|怎么(?:理解|判断|预防|识别)|科普|举例|解释一下|介绍一下"
)
_CONDITIONAL = re.compile(r"如果|假如|假设|万一|一旦|倘若|要是|可能会|会不会|担心会")
_PAST = re.compile(r"曾经|昨天|昨晚|前天|以前|之前|过去|上周|上个月|去年|[\d一二三四五六七八九十几]+天前")
_CURRENT = re.compile(r"现在|此刻|目前|正在|刚刚|刚才|今天")
_NEGATED = re.compile(r"没有|没|未|无|不是|并非|否认|不曾|不伴|不再|不觉得|不感觉|有无|是否|有没有")
_RESOLVED = re.compile(r"^(?:现(?:在)?|已(?:经)?)?(?:没有了|没有$|没了|消失了?|不痛了|不疼了|好了)")
_PAST_SUFFIX = re.compile(r"^(?:是|发生在)(?:昨天|昨晚|前天|以前|过去)")
_SEPARATOR = re.compile(r"([，,。！？!?；;\n]|但是|但|可是|然而|而是|(?<!喘)不过)")
_STRONG_BOUNDARY = re.compile(r"^[。！？!?；;\n]")
_CURRENT_CLAUSE_JOIN = re.compile(r"(?:并且|且)(?=现在)")

_UNIT = r"(?:摄氏度|°\s*[cC]|度)"
_TEMPERATURE = re.compile(
    rf"(?<![\d.])(?P<low>\d{{2}}(?:\.\d+)?)\s*"
    rf"(?:(?:{_UNIT})?\s*[-~～—至到]\s*(?P<high>\d{{2}}(?:\.\d+)?)\s*)?{_UNIT}"
)
_BODY_TEMPERATURE = re.compile(r"体温|发烧|发热|高热|烧到|烧至|腋温|口温|耳温")
_OTHER_TEMPERATURE = re.compile(r"气温|室温|水温|天气|空调|烤箱|冰箱|旋转|角度|水烧|牛奶")
_PATIENT = re.compile(r"我|他|她|孩子|宝宝|老人|爸爸|妈妈|患者|病人")
_UPPER_BOUND = re.compile(r"(?:不超过|不高于|不到|低于|小于|至多|最多|[<≤]|<=)\s*$")
_STRICT_LOWER_BOUND = re.compile(r"(?:超过|高于|大于|>)\s*$")


def _temperature_signals(clause: str):
    """只接受摄氏温度；区间两端均须 >39.5，不把上界当成实测值。"""
    for match in _TEMPERATURE.finditer(clause):
        prefix, suffix = clause[:match.start()], clause[match.end():]
        # 明确的非人体温度不能触发；同句后续明确体温可重新建立语境。
        body = list(_BODY_TEMPERATURE.finditer(prefix))
        other = list(_OTHER_TEMPERATURE.finditer(prefix))
        if other and (not body or other[-1].end() > body[-1].start()):
            continue
        if not body and not _PATIENT.search(prefix) and prefix.strip() not in ("", "现在", "目前"):
            continue
        if _UPPER_BOUND.search(prefix) or re.match(r"\s*(?:以下|以内|不到|华氏|[fF])", suffix):
            continue
        low = float(match["low"])
        high = float(match["high"]) if match["high"] else low
        # 只解释有界的人体摄氏温度写法，不将异常数字或华氏数值换算为事实。
        if not (30 <= low <= 45 and 30 <= high <= 45):
            continue
        strictly_above = min(low, high) > 39.5
        if match["high"] is None and low == 39.5 and _STRICT_LOWER_BOUND.search(prefix):
            strictly_above = True
        if strictly_above:
            yield match


def _signals(clause: str):
    yield from _SYMPTOM.finditer(clause)
    yield from _temperature_signals(clause)


def _last_position(pattern: re.Pattern, text: str) -> int:
    return max((match.start() for match in pattern.finditer(text)), default=-1)


def detect_emergency(message: str) -> bool:
    """匹配有限的当前肯定描述；不命中不代表排除急症或适合等待。"""
    if not isinstance(message, str):
        return False
    # NFKC 统一全角数字、℃/°C；小数点不作为分句符。
    text = unicodedata.normalize("NFKC", message)
    # 仅明确“并且现在/且现在”开启新分句；“没有呼吸困难和胸痛”仍是并列否定。
    # 使用逗号边界，保留外层假设/知识语境，不把假设变成正在发生的事实。
    text = _CURRENT_CLAUSE_JOIN.sub("，", text)
    scope = ""
    for clause in _SEPARATOR.split(text):
        if _SEPARATOR.fullmatch(clause):
            if _STRONG_BOUNDARY.match(clause):
                scope = ""
            continue
        if _REFERENCE.search(clause):
            scope = "reference"
            continue
        for match in _signals(clause):
            prefix, suffix = clause[:match.start()], clause[match.end():].strip()
            current = _last_position(_CURRENT, prefix)
            if scope in ("conditional", "reference") or (scope == "past" and current < 0):
                continue
            if _CONDITIONAL.search(prefix) or _last_position(_PAST, prefix) > current:
                continue
            if _NEGATED.search(prefix) or _RESOLVED.match(suffix) or _PAST_SUFFIX.match(suffix):
                continue
            if suffix.startswith(("吗", "是否", "有没有")):
                continue
            return True
        # 逗号不能把“如果发烧，体温40度”或“昨天发烧，体温40度”变成当前事实。
        if _CONDITIONAL.search(clause):
            scope = "conditional"
        elif _last_position(_PAST, clause) > _last_position(_CURRENT, clause):
            scope = "past"
        elif _CURRENT.search(clause) and scope == "past":
            scope = ""
    return False


def is_emergency_reference(message: str) -> bool:
    """含候选信号但并非当前肯定命中，供分类/编排同步排除。"""
    return (isinstance(message, str)
            and any(_signals(unicodedata.normalize("NFKC", message)))
            and not detect_emergency(message))
