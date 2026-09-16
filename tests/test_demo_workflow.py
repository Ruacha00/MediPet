"""I04：固定业务时钟，真实 API/Agent 工具/服务，模型与存储使用替身。"""
import json

import pytest

from agents.agent_orchestrator import AgentType
from core.intent_recognizer import IntentCategory, IntentResult, UrgencyLevel
from hospital.models import VisitIdentity
from test_agent_orchestrator import SequenceClient, text_blocks, tool_blocks
from test_chat_api import wired_api  # 显式引入跨模块 fixture，保持相同的 API 接线。


def classify(ctx, intent):
    ctx.orch.recognize_intent.side_effect = None
    ctx.orch.recognize_intent.return_value = IntentResult(
        intent, .95, UrgencyLevel.LOW, "appointment" if intent.name.startswith("APPOINTMENT") or intent == IntentCategory.SLOT_QUERY else "query",
        {}, "确定性接口验收分类", 0,
    )


def only_artifact(data, kind):
    matches = [artifact for artifact in data["artifacts"] if artifact["type"] == kind]
    assert len(matches) == 1
    return matches[0]["data"]


async def child_query(ctx, conv):
    classify(ctx, IntentCategory.SLOT_QUERY)
    ctx.orch._pool[AgentType.APPOINTMENT][0]._client = SequenceClient(
        tool_blocks("search_slots", {"department": "儿科", "date": "明天"}), text_blocks("以下是明天儿科号源。"),
    )
    ctx.orch._pool[AgentType.GUIDANCE][0]._client = SequenceClient(
        tool_blocks("get_visit_checklist", {"visit_type": "child"}), text_blocks("请按儿童清单携带就诊资料。"),
    )
    ctx.orch._composer._client = SequenceClient(text_blocks("儿科号源与儿童就诊材料已列出，请核对。"))
    response = await ctx.client.post("/chat", json={"conv_id": conv, "message": "查明天儿科号源，并告诉我要带什么材料"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data["agent_types"]) == {"appointment", "guidance"}
    assert {item["type"] for item in data["artifacts"]} == {"slot_list", "visit_checklist"}
    calls = [trace for trace in data["tool_traces"] if trace["kind"] == "tool_call"]
    assert {trace["tool_name"] for trace in calls} == {"search_slots", "get_visit_checklist"}
    assert all(trace["success"] for trace in calls)
    return data


async def appointment_turn(ctx, conv, message, intent, tool, args, reply="请查看本次业务资料。"):
    classify(ctx, intent)
    ctx.orch._pool[AgentType.APPOINTMENT][0]._client = SequenceClient(tool_blocks(tool, args), text_blocks(reply))
    response = await ctx.client.post("/chat", json={"conv_id": conv, "message": message})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["primary_agent"] == "appointment"
    assert any(trace.get("tool_name") == tool for trace in data["tool_traces"])
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize("run_number", [1, 2])
async def test_child_booking_query_cancel_workflow_is_repeatable(wired_api, run_number):
    ctx = wired_api
    created_visit = await ctx.client.post("/visits", json={"patient_id": "patient_child", "title": f"儿童就诊演示{run_number}"})
    assert created_visit.status_code == 201
    visit = created_visit.json()
    identity = VisitIdentity(user_id=visit["user_id"], patient_id=visit["patient_id"], conv_id=visit["conv_id"])
    conv = identity.conv_id
    queried = await child_query(ctx, conv)
    slots = only_artifact(queried, "slot_list")["slots"]
    assert slots and {slot["date"] for slot in slots} == {"2026-09-17"}
    prepared = await appointment_turn(ctx, conv, "选择第一个号", IntentCategory.APPOINTMENT_CREATE,
                                      "prepare_appointment", {"selection_index": 1})
    proposal = only_artifact(prepared, "appointment_proposal")
    assert proposal["status"] == "pending" and proposal["target_id"] == slots[0]["slot_id"]
    assert proposal["patient_id"] == "patient_child"
    assert (await ctx.service.get_appointments(identity)).data["items"] == []
    assert (await ctx.service.store.get_slot(proposal["target_id"])).remaining == slots[0]["remaining"]

    text_confirmed = await appointment_turn(ctx, conv, "确认预约", IntentCategory.APPOINTMENT_CREATE,
                                            "prepare_appointment", {}, "请点击确认按钮完成预约。")
    assert only_artifact(text_confirmed, "appointment_proposal") == proposal
    assert (await ctx.service.get_appointments(identity)).data["items"] == []
    assert (await ctx.service.store.get_slot(proposal["target_id"])).remaining == slots[0]["remaining"]

    confirm_url = f"/appointment-proposals/{proposal['proposal_id']}/confirm"
    confirmed = await ctx.client.post(confirm_url, json={"conv_id": conv})
    assert confirmed.status_code == 200, confirmed.text
    receipt = confirmed.json()["receipt"]
    appointment_id = receipt["appointment"]["appointment_id"]
    assert receipt["appointment"]["status"] == "active"
    assert set(confirmed.json()) == {"user_id", "patient_id", "conv_id", "receipt", "artifacts"}
    assert (await ctx.client.post(confirm_url, json={"conv_id": conv})).json() == confirmed.json()
    assert (await ctx.service.store.get_slot(proposal["target_id"])).remaining == slots[0]["remaining"] - 1

    queried_record = await appointment_turn(ctx, conv, "查询我的预约记录", IntentCategory.APPOINTMENT_STATUS,
                                            "list_appointments", {})
    assert only_artifact(queried_record, "appointment_record")["appointment_id"] == appointment_id
    model = ctx.orch._pool[AgentType.APPOINTMENT][0]._client
    assert "预约已完成。" in json.dumps(model.calls[0]["messages"], ensure_ascii=False)
    cancel_prepared = await appointment_turn(ctx, conv, "取消这条预约", IntentCategory.APPOINTMENT_CANCEL,
                                             "prepare_cancellation", {"appointment_id": appointment_id})
    cancellation = only_artifact(cancel_prepared, "appointment_proposal")
    assert cancellation["status"] == "pending" and cancellation["operation"] == "cancel"
    assert (await ctx.service.get_appointments(identity)).data["items"][0]["status"] == "active"
    cancel_url = f"/appointment-proposals/{cancellation['proposal_id']}/confirm"
    cancelled = await ctx.client.post(cancel_url, json={"conv_id": conv})
    assert cancelled.status_code == 200
    assert cancelled.json()["receipt"]["appointment"]["status"] == "cancelled"
    assert (await ctx.client.post(cancel_url, json={"conv_id": conv})).json() == cancelled.json()
    records = (await ctx.service.get_appointments(identity)).data["items"]
    assert len(records) == 1 and records[0]["appointment_id"] == appointment_id and records[0]["status"] == "cancelled"
    assert (await ctx.service.store.get_slot(proposal["target_id"])).remaining == slots[0]["remaining"]

    history = (await ctx.client.get(f"/visits/{conv}/messages")).json()["items"]
    assert len(history) == 14 and len({message["message_id"] for message in history}) == 14
    assert history[1]["artifacts"] == queried["artifacts"]
    results = [message for message in history if message["kind"] == "operation_result"]
    assert [message["metadata"]["receipt"] for message in results] == [receipt, cancelled.json()["receipt"]]
    assert all("tool_traces" not in message["metadata"] for message in results)
    assert (await ctx.client.patch(f"/visits/{conv}", json={"title": "儿童预约已取消"})).status_code == 200
    assert (await ctx.client.patch(f"/visits/{conv}", json={"archived": True})).status_code == 200
    blocked = await ctx.client.post("/chat", json={"conv_id": conv, "message": "继续"})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "visit_archived"
    assert (await ctx.client.get(f"/visits/{conv}/messages")).json()["items"] == history
    assert (await ctx.client.patch(f"/visits/{conv}", json={"archived": False})).status_code == 200
    restored = (await ctx.client.get(f"/visits/{conv}/messages")).json()
    assert restored["items"] == history and restored["visit"]["title"] == "儿童预约已取消"


@pytest.mark.asyncio
async def test_patient_switch_keeps_selection_history_and_appointments_separate(wired_api):
    ctx = wired_api
    visits = [(await ctx.client.post("/visits", json={"patient_id": patient})).json()
              for patient in ("patient_child", "patient_self")]
    child, own = visits
    await child_query(ctx, child["conv_id"])
    prepared = await appointment_turn(ctx, child["conv_id"], "选择第一个号", IntentCategory.APPOINTMENT_CREATE,
                                      "prepare_appointment", {"selection_index": 1})
    proposal = only_artifact(prepared, "appointment_proposal")
    url = f"/appointment-proposals/{proposal['proposal_id']}/confirm"
    assert (await ctx.client.post(url, json={"conv_id": child["conv_id"]})).status_code == 200
    child_history = (await ctx.client.get(f"/visits/{child['conv_id']}/messages")).json()["items"]

    rejected = await appointment_turn(ctx, own["conv_id"], "选择第一个号", IntentCategory.APPOINTMENT_CREATE,
                                      "prepare_appointment", {"selection_index": 1})
    assert ctx.orch.recognize_intent.call_args.kwargs["history"] is None
    assert rejected["artifacts"] == []
    assert any(trace.get("error_code") == "selection_unavailable" and not trace["success"] for trace in rejected["tool_traces"])
    empty = await appointment_turn(ctx, own["conv_id"], "查询我的预约记录", IntentCategory.APPOINTMENT_STATUS,
                                   "list_appointments", {})
    assert empty["artifacts"] == []
    assert all(trace["success"] for trace in empty["tool_traces"] if trace["kind"] == "tool_call")
    assert (await ctx.client.post(url, json={"conv_id": own["conv_id"]})).status_code == 403
    assert (await ctx.client.post("/chat", json={"conv_id": child["conv_id"], "patient_id": "patient_self", "message": "继续"})).status_code == 403
    assert (await ctx.visits.get_selection("anonymous", own["conv_id"])).slots == []
    assert (await ctx.visits.get_selection("anonymous", child["conv_id"])).slots
    own_history = (await ctx.client.get(f"/visits/{own['conv_id']}/messages")).json()["items"]
    assert {message["patient_id"] for message in own_history} == {"patient_self"}
    assert all(message["kind"] == "chat" for message in own_history)
    assert (await ctx.client.get(f"/visits/{child['conv_id']}/messages")).json()["items"] == child_history


@pytest.mark.asyncio
async def test_compression_and_expiry_restore_full_history_cards_and_selection(wired_api):
    ctx = wired_api
    visit = (await ctx.client.post("/visits", json={"patient_id": "patient_child"})).json()
    conv = visit["conv_id"]
    identity = VisitIdentity(user_id="anonymous", patient_id="patient_child", conv_id=conv)
    first = await child_query(ctx, conv)
    slot_id = only_artifact(first, "slot_list")["slots"][0]["slot_id"]
    classify(ctx, IntentCategory.HOSPITAL_INFO)
    for _ in range(7):
        response = await ctx.client.post("/chat", json={"conv_id": conv, "message": "继续介绍医院"})
        assert response.status_code == 200, response.text
    history = (await ctx.client.get(f"/visits/{conv}/messages")).json()["items"]
    assert len(history) == 16 and history[1]["artifacts"] == first["artifacts"]
    assert len(await ctx.memory._get_working_memory(identity)) < len(history)
    assert await ctx.redis.get(ctx.memory._summary_key(identity))
    assert await ctx.redis.ttl(ctx.visits.messages_key(conv)) == -1
    # 内存替身通过移除窗口/摘要模拟过期；真实 TTL 已由 V03 单独验收。
    await ctx.redis.delete(ctx.memory._wm_key(identity), ctx.memory._summary_key(identity))
    assert (await ctx.client.get(f"/visits/{conv}/messages")).json()["items"] == history
    continued = await appointment_turn(ctx, conv, "就选第一个号", IntentCategory.APPOINTMENT_CREATE,
                                       "prepare_appointment", {"selection_index": 1})
    assert only_artifact(continued, "appointment_proposal")["target_id"] == slot_id
    model_context = json.dumps(ctx.orch._pool[AgentType.APPOINTMENT][0]._client.calls[0]["messages"], ensure_ascii=False)
    assert slot_id in model_context and "序号从1开始" in model_context
    assert ctx.orch.recognize_intent.call_args.kwargs["history"]
    after = (await ctx.client.get(f"/visits/{conv}/messages")).json()["items"]
    assert after[:16] == history and len(after) == 18
