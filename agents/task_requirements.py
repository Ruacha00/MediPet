"""Small read-only delivery requirements; never authorizes appointment writes."""
import re


def active_request_text(message: str) -> str:
    """Exclude explicitly declined clauses when detecting additional tasks."""
    clauses = re.split(r"[，,。；;！？!?\n]|(?:但是|不过|而是|但)", message)
    return "，".join(clause for clause in clauses if not re.search(
        r"(?:不需要|不用|无需|不要|不必|暂不|别)(?:再|帮我|给我|查询|查|提供|列出|说|讲|了解)?", clause,
    ))


def wants_checklist(message: str) -> bool:
    if re.search(r"(?:不需要|不用|无需|不要|不必|暂不|别)[^，,。；;！？!?\n]{0,12}(?:材料|资料|清单|证件|要带)", message):
        return False
    text = active_request_text(message)
    return bool(re.search(
        r"材料|就诊准备|准备(?:什么|哪些|清单|事项)|(?:需要的|所需|就诊|携带|带哪些|需要哪些)资料"
        r"|(?:带|携带)(?:什么|哪些|的东西|的证件)|(?:就诊|到院).{0,12}证件", text,
    ))


def declines_slot_query(message: str) -> bool:
    return bool(re.search(
        r"(?:不需要|不用|无需|不要|不必|暂不|别)"
        r"(?:再|实际|帮我|给我|执行|进行|去|真的|马上|立即|继续|直接|主动|自动|任何|本次|提供|查询|查|搜索|列出|显示|查看|的)*"
        r"(?:号源|门诊号|查号|时段)", message,
    ))


def wants_slots(message: str) -> bool:
    if declines_slot_query(message):
        return False
    text = active_request_text(message)
    return bool(re.search(
        r"号源|查号|查.{0,12}的号|门诊号|(?:还有|有|哪些|剩余的|能约的|可预约)号|(?:可选|可预约|有号).{0,6}时段|时段有号", text,
    ))


def compound_visit_request(message: str) -> bool:
    return wants_slots(message) and wants_checklist(message)


def explicit_visit_type(message: str) -> str | None:
    """Use explicit visit requirements, never infer age from a patient ID/family relationship."""
    text = active_request_text(message)
    # A prior subject mentioned in the same message must not override the
    # current visit requirements. Retain the latest explicitly selected subject.
    switches = list(re.finditer(r"(?:现在|这次|本次).{0,5}(?:本人|自己|孩子|儿童|小孩|宝宝|幼儿|成人)", text))
    if switches:
        text = text[switches[-1].start():]
    if not wants_checklist(text):
        return None
    child = bool(re.search(r"儿童|孩子|小孩|宝宝|幼儿", text))
    adult = bool(re.search(r"成人|成年人|本人|自己", text))
    if child and adult:
        return None
    if child:
        return "child"
    if re.search(r"首次|第一次|初次|初诊", text):
        return "first"
    return None
