from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph


class DemoAgentState(TypedDict):
    message: str
    reply: str
    data_parts: list[dict[str, Any]]


EMERGENCY_SIGNALS = ("呼吸困难", "昏迷", "剧烈胸痛", "大出血")


def _route_visit(state: DemoAgentState) -> dict[str, Any]:
    message = state["message"].strip()
    lowered = message.lower()

    if any(signal in message for signal in EMERGENCY_SIGNALS):
        return {
            "reply": (
                "你描述的情况可能需要紧急评估。请立即联系医院急诊或当地急救渠道，"
                "不要等待本助手继续安排普通门诊。"
            ),
            "data_parts": [
                {
                    "type": "data-handoff",
                    "id": "handoff-emergency",
                    "data": {
                        "priority": "emergency",
                        "title": "请立即寻求紧急帮助",
                        "description": "普通门诊协助已暂停。",
                    },
                }
            ],
        }

    if "挂号" in message or "号源" in message or "预约" in message:
        return {
            "reply": "我找到了一个演示号源。请核对预约信息，确认前不会创建挂号。",
            "data_parts": [
                {
                    "type": "data-slot-options",
                    "id": "slots-demo",
                    "data": {
                        "department": "全科医学科",
                        "slots": [
                            {
                                "id": "slot-demo-001",
                                "doctor": "林医生",
                                "date": "2026-09-01",
                                "time": "09:30",
                                "fee": 25,
                            }
                        ],
                    },
                },
                {
                    "type": "data-action-proposal",
                    "id": "proposal-demo-001",
                    "data": {
                        "proposalId": "proposal-demo-001",
                        "action": "create",
                        "patient": "演示患者",
                        "department": "全科医学科",
                        "doctor": "林医生",
                        "date": "2026-09-01",
                        "time": "09:30",
                        "fee": 25,
                        "status": "pending",
                    },
                },
            ],
        }

    if "怎么走" in message or "导航" in message or "路线" in message:
        return {
            "reply": "这里是院内演示路线。到院后请以现场标识和工作人员指引为准。",
            "data_parts": [
                {
                    "type": "data-hospital-route",
                    "id": "route-demo",
                    "data": {
                        "destination": "门诊二层 · 全科医学科",
                        "steps": ["门诊大厅签到", "乘扶梯前往二层", "在 A 区候诊"],
                    },
                }
            ],
        }

    symptom_candidates: list[dict[str, str]] = []
    if "皮肤" in message or "皮疹" in message:
        symptom_candidates.append(
            {"name": "皮肤科", "reason": "演示导诊规则匹配到皮肤相关不适"}
        )
    if "头痛" in message:
        symptom_candidates.append(
            {"name": "神经内科", "reason": "演示导诊规则匹配到头痛描述"}
        )
    if "咳嗽" in message or "发热" in message:
        symptom_candidates.append(
            {"name": "呼吸内科", "reason": "演示导诊规则匹配到呼吸道相关描述"}
        )

    if symptom_candidates:
        symptom_candidates.append(
            {"name": "全科医学科", "reason": "信息不足时可先由全科进一步评估"}
        )
        return {
            "reply": (
                "根据医院演示导诊规则，我整理了候选科室。"
                "这些结果用于就诊引导，不代表疾病诊断。"
            ),
            "data_parts": [
                {
                    "type": "data-department-candidates",
                    "id": "departments-demo",
                    "data": {
                        "candidates": symptom_candidates,
                        "uncertainty": "中",
                    },
                }
            ],
        }

    if lowered in {"你好", "hello", "hi"}:
        reply = "你好，我是 MediPet。你可以告诉我主要不适，或询问科室、号源和院内路线。"
    else:
        reply = (
            "我可以协助科室引导、查询号源、准备预约和查看院内路线。"
            "请先告诉我这次就诊最想解决什么问题。"
        )
    return {"reply": reply, "data_parts": []}


def build_demo_graph():
    graph = StateGraph(DemoAgentState)
    graph.add_node("route_visit", _route_visit)
    graph.add_edge(START, "route_visit")
    graph.add_edge("route_visit", END)
    return graph.compile()
