"""MediPet HTTP 业务契约与确定性接线验证。"""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException

from api import main
from core.skill_loader import SkillManager
from hospital.models import Visit, VisitMessage
from hospital.service import HospitalService
from memory.visit_store import VisitStore
from test_visit_memory import MemoryRedis
from test_appointment_flow import BusinessRedis
from test_conversation_memory import Chroma, Model
from test_agent_orchestrator import make_orchestrator, SequenceClient, text_blocks, tool_blocks, FakeClient
from agents.agent_orchestrator import AgentType
from agents.tools import build_hospital_tools
from core.intent_recognizer import IntentCategory, IntentResult, UrgencyLevel
from hospital.models import VisitIdentity
from hospital.store import HospitalStore
from memory.conversation_memory import MemoryManager
from unittest.mock import AsyncMock


@pytest_asyncio.fixture
async def api_client(monkeypatch):
    store = VisitStore(MemoryRedis(), prefix="medipet:test:api:")
    await store.initialize_patients(HospitalService().data.patients)
    monkeypatch.setattr(main, "_visit_store", store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://medipet.test") as client:
        yield SimpleNamespace(client=client, store=store)


@pytest.mark.asyncio
async def test_patients_create_list_rename_archive_restore_and_history(api_client):
    client, store = api_client.client, api_client.store
    patients = (await client.get("/patients")).json()["items"]
    assert [item["patient_id"] for item in patients] == ["patient_self", "patient_child"]
    created = await client.post("/visits", json={"patient_id": "patient_child", "title": "儿童初诊"})
    assert created.status_code == 201
    visit = Visit.model_validate(created.json())
    card = HospitalService().get_visit_checklist().artifacts[0]
    message = VisitMessage(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id,
                           message_id="message-1", role="assistant", content="首次就诊材料", artifacts=[card], created_at=datetime.now(timezone.utc))
    await store.append_messages(visit.user_id, visit.conv_id, [message])
    assert (await client.get("/visits", params={"patient_id": "patient_self"})).json() == {"items": []}
    items = (await client.get("/visits", params={"patient_id": "patient_child"})).json()["items"]
    assert len(items) == 1 and items[0]["conv_id"] == visit.conv_id
    renamed = await client.patch(f"/visits/{visit.conv_id}", json={"title": "复诊准备"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "复诊准备"
    original = (await client.get(f"/visits/{visit.conv_id}/messages")).json()["items"]
    assert original == [message.model_dump(mode="json")]
    for archived in (True, False):
        changed = await client.patch(f"/visits/{visit.conv_id}", json={"archived": archived})
        assert changed.status_code == 200 and changed.json()["archived"] == archived
        assert (await client.get(f"/visits/{visit.conv_id}/messages")).json()["items"] == original
        visible = (await client.get("/visits", params={"patient_id": "patient_child"})).json()["items"]
        assert len(visible) == (0 if archived else 1)
        assert len((await client.get("/visits", params={"patient_id": "patient_child", "archived": archived})).json()["items"]) == 1


@pytest.mark.asyncio
async def test_visit_api_rejects_identity_changes_and_preserves_native_validation(api_client):
    client = api_client.client
    visit = (await client.post("/visits", json={"patient_id": "patient_child"})).json()
    conv = visit["conv_id"]
    assert (await client.get("/patients", params={"user_id": "other"})).json() == {"items": []}
    forbidden = [await client.post("/visits", json={"user_id": "other", "patient_id": "patient_child"}),
                 await client.get(f"/visits/{conv}/messages", params={"user_id": "other"}),
                 await client.patch(f"/visits/{conv}", json={"user_id": "other", "title": "越权改名"})]
    assert all(response.status_code == 403 and response.json()["detail"]["code"] == "identity_conflict" for response in forbidden)
    for body in ({"patient_id": "patient_self"}, {"archived": "false"}, {"title": " "}):
        rejected = await client.patch(f"/visits/{conv}", json=body)
        assert rejected.status_code == 422 and isinstance(rejected.json()["detail"], list)
    empty = await client.patch(f"/visits/{conv}", json={})
    assert empty.status_code == 422 and empty.json()["detail"]["code"] == "missing_fields"
    missing = await client.get("/visits/missing/messages")
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "not_found"
    assert (await client.get(f"/visits/{conv}/messages")).json()["visit"]["patient_id"] == "patient_child"


@pytest.mark.asyncio
async def test_shared_visit_loader_defaults_to_self_and_rejects_conflicting_existing_visit(api_client):
    own = await main._load_visit("anonymous")
    assert own.patient_id == "patient_self"
    child = await main._load_visit("anonymous", patient_id="patient_child")
    assert child.patient_id == "patient_child"
    assert await main._load_visit("anonymous", child.conv_id) == child
    for conv, patient, status in ((child.conv_id, "patient_self", 403), ("unknown-visit", None, 404)):
        with pytest.raises(HTTPException) as rejected:
            await main._load_visit("anonymous", conv, patient)
        assert rejected.value.status_code == status
    await api_client.store.update_visit("anonymous", child.conv_id, archived=True)
    with pytest.raises(HTTPException) as archived:
        await main._load_visit("anonymous", child.conv_id)
    assert archived.value.status_code == 409


@pytest.mark.asyncio
async def test_health_skills_and_reload_still_work_with_visit_routes(api_client, monkeypatch):
    manager = SkillManager(str(Path(__file__).parents[1] / "skills"))
    manager.load()
    monkeypatch.setattr(main, "_skill_manager", manager)
    attached = []
    monkeypatch.setattr(main, "_orchestrator", SimpleNamespace(get_stats=lambda: {"ready": True}, set_skill_manager=attached.append))
    assert (await api_client.client.get("/health")).json() == {"status": "ok", "agents": {"ready": True}}
    assert (await api_client.client.get("/skills")).json()["count"] == 6
    assert (await api_client.client.post("/skills/reload")).json()["count"] == 6
    assert attached == [manager]


@pytest.mark.asyncio
async def test_visit_storage_failure_has_explicit_retryable_business_error(api_client):
    api_client.store.redis.fail = True
    response = await api_client.client.get("/patients")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "storage_unavailable" and response.json()["detail"]["retryable"] is True


@pytest_asyncio.fixture
async def wired_api(monkeypatch):
    clock = SimpleNamespace(value=datetime.fromisoformat("2026-09-16T10:00:00+08:00"))
    redis = BusinessRedis()
    visits = VisitStore(redis, prefix="medipet:test:wired:", clock=lambda: clock.value)
    service = HospitalService(store=HospitalStore(redis, visits.prefix), visit_store=visits, clock=lambda: clock.value)
    await visits.initialize_patients(service.data.patients)
    await service.initialize_slots()
    memory = MemoryManager(api_key="test", redis_client=redis, chroma_client=Chroma(), visit_store=visits)
    memory._client = Model()
    orchestrator = make_orchestrator()
    for pool in orchestrator._pool.values():
        for agent in pool:
            agent._hospital_tools = build_hospital_tools(agent.agent_type.value, service, visits)
    async def recognize(message, history=None):
        return IntentResult(IntentCategory.HOSPITAL_INFO, .9, UrgencyLevel.LOW, "query", {}, "固定测试分类", 0)
    orchestrator.recognize_intent = AsyncMock(side_effect=recognize)
    for name, value in (("_memory", memory), ("_orchestrator", orchestrator), ("_visit_store", visits), ("_hospital_service", service)):
        monkeypatch.setattr(main, name, value)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://medipet.test") as client:
        yield SimpleNamespace(client=client, visits=visits, service=service, memory=memory, redis=redis, clock=clock, orch=orchestrator)


@pytest.mark.asyncio
async def test_chat_server_identity_full_history_and_restored_window(wired_api):
    ctx = wired_api
    first = await ctx.client.post("/chat", json={"message": "医院信息", "patient_id": "patient_child"})
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["patient_id"] == data["visit"]["patient_id"] == "patient_child"
    conv = data["conv_id"]
    identity = VisitIdentity(user_id="anonymous", patient_id="patient_child", conv_id=conv)
    history = (await ctx.client.get(f"/visits/{conv}/messages")).json()["items"]
    assert len(history) == 2 and history[1]["metadata"]["request_id"] == data["request_id"]
    assert (await ctx.client.post("/chat", json={"message": "继续", "conv_id": conv, "patient_id": "patient_self"})).status_code == 403
    assert (await ctx.client.post("/chat", json={"message": "继续", "conv_id": "missing"})).status_code == 404
    await ctx.client.patch(f"/visits/{conv}", json={"archived": True})
    assert (await ctx.client.post("/chat", json={"message": "继续", "conv_id": conv})).status_code == 409
    await ctx.client.patch(f"/visits/{conv}", json={"archived": False})
    await ctx.redis.delete(ctx.memory._wm_key(identity))
    again = await ctx.client.post("/chat", json={"message": "再说一遍", "conv_id": conv})
    assert again.status_code == 200
    assert ctx.orch.recognize_intent.call_args.kwargs["history"][0]["content"] == "医院信息"
    own = (await ctx.client.post("/chat", json={"message": "您好"})).json()
    assert own["patient_id"] == "patient_self" and own["conv_id"] != conv
    assert ctx.orch.recognize_intent.call_args.kwargs["history"] is None


@pytest.mark.asyncio
async def test_emergency_skips_memory_recall_and_all_models_but_keeps_history_and_window(wired_api, monkeypatch):
    from core.emergency import EMERGENCY_RESPONSE
    ctx = wired_api
    forbidden = AsyncMock(side_effect=AssertionError("急症不可调用普通模型/记忆"))
    monkeypatch.setattr(ctx.memory, "get_context", forbidden)
    monkeypatch.setattr(ctx.memory, "update_profile", forbidden)
    ctx.memory._client.messages.create = forbidden
    ctx.orch.recognize_intent = forbidden
    for pool in ctx.orch._pool.values():
        for agent in pool:
            agent._client = SimpleNamespace(messages=SimpleNamespace(create=forbidden))
    response = await ctx.client.post("/chat", json={"message": "现在呼吸困难", "patient_id": "patient_child"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert EMERGENCY_RESPONSE in data["response"] and data["intent"] == "emergency"
    assert data["tool_traces"] == [] and data["tools_used"] == []
    assert data["artifacts"][0]["type"] == "contact_info"
    assert forbidden.call_count == 0
    history = (await ctx.client.get(f"/visits/{data['conv_id']}/messages")).json()["items"]
    assert history[1]["artifacts"] == data["artifacts"]
    identity = VisitIdentity(user_id="anonymous", patient_id="patient_child", conv_id=data["conv_id"])
    window = await ctx.memory._get_working_memory(identity)
    assert len(window) == 2 and window[0].content == "现在呼吸困难"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["我没有现在呼吸困难", "现在剧烈胸痛是什么意思？"])
async def test_emergency_counterexamples_follow_normal_chat(wired_api, message):
    response = await wired_api.client.post("/chat", json={"message": message})
    assert response.status_code == 200 and response.json()["intent"] != "emergency"
    assert wired_api.orch.recognize_intent.call_count == 1


@pytest.mark.asyncio
async def test_chat_parallel_cards_and_failed_tool_trace_are_preserved(wired_api):
    ctx = wired_api
    ctx.orch.recognize_intent.return_value = IntentResult(IntentCategory.SLOT_QUERY, .9, UrgencyLevel.LOW, "appointment",
                                                          {"department": ["儿科"], "date": ["2026-09-17"]}, "测试", 0)
    ctx.orch.recognize_intent.side_effect = None
    ctx.orch._pool[AgentType.APPOINTMENT][0]._client = SequenceClient(tool_blocks("search_slots", {"department": "儿科", "date": "明天"}), text_blocks("以下是实时号源。"))
    ctx.orch._pool[AgentType.GUIDANCE][0]._client = SequenceClient(tool_blocks("get_visit_checklist", {"visit_type": "first"}), text_blocks("请带证件。"))
    response = await ctx.client.post("/chat", json={"message": "查明天儿科号源并告诉我要带什么材料", "patient_id": "patient_child"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert {a["type"] for a in data["artifacts"]} == {"slot_list", "visit_checklist"}
    assert set(data["agent_types"]) == {"appointment", "guidance"}
    assert len(data["tool_traces"]) >= 2
    history = (await ctx.client.get(f"/visits/{data['conv_id']}/messages")).json()["items"]
    assert history[-1]["artifacts"] == data["artifacts"]
    assert history[-1]["metadata"]["tool_traces"] == data["tool_traces"]
    ctx.orch._pool[AgentType.APPOINTMENT][0]._client = SequenceClient(tool_blocks("search_slots", {"date": "2030-01-01"}), text_blocks("没有可用号源。"))
    failed = (await ctx.client.post("/chat", json={"message": "查号源", "conv_id": data["conv_id"]})).json()
    assert failed["artifacts"][0]["data"]["slots"] == []
    assert any(t.get("error_code") == "no_slots" and not t["success"] for t in failed["tool_traces"])
    assert not failed["knowledge_used"]


async def prepare_for_api(ctx, patient="patient_child"):
    visit = await ctx.visits.create_visit("anonymous", patient)
    identity = VisitIdentity(user_id=visit.user_id, patient_id=patient, conv_id=visit.conv_id)
    slots = await ctx.service.search_slots(department="儿科", date="明天")
    proposal = await ctx.service.prepare_appointment(identity, slot_id=slots.data["slots"][0]["slot_id"])
    assert proposal.success
    return identity, proposal.data


@pytest.mark.asyncio
async def test_confirm_and_cancel_replay_original_receipt_and_history_without_models(wired_api):
    ctx = wired_api
    identity, proposal = await prepare_for_api(ctx)
    assert (await ctx.service.get_appointments(identity)).data["items"] == []
    from memory.conversation_memory import MsgRole
    await ctx.memory.add_message(identity, MsgRole.ASSISTANT, "当前方案尚未确认。")
    url = f"/appointment-proposals/{proposal['proposal_id']}/confirm"
    response = await ctx.client.post(url, json={"conv_id": identity.conv_id})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["receipt"]["appointment"]["status"] == "active"
    assert "request_id" not in data and "tool_traces" not in data
    assert (await ctx.client.post(url, json={"conv_id": identity.conv_id})).json() == data
    history = (await ctx.client.get(f"/visits/{identity.conv_id}/messages")).json()["items"]
    assert [m["kind"] for m in history] == ["confirmation_event", "operation_result"]
    assert history[1]["metadata"]["receipt"] == data["receipt"]
    restored = await ctx.memory.get_context(identity)
    assert restored.recent_messages[-1].content == "预约已完成。"
    assert restored.recent_messages[-1].metadata["artifacts"] == data["artifacts"]
    cancellation = await ctx.service.prepare_cancellation(identity, data["receipt"]["appointment"]["appointment_id"])
    assert (await ctx.service.get_appointments(identity)).data["items"][0]["status"] == "active"
    cancel_url = f"/appointment-proposals/{cancellation.data['proposal_id']}/confirm"
    cancelled = await ctx.client.post(cancel_url, json={"conv_id": identity.conv_id})
    assert cancelled.status_code == 200 and cancelled.json()["receipt"]["appointment"]["status"] == "cancelled"
    assert (await ctx.client.post(cancel_url, json={"conv_id": identity.conv_id})).json() == cancelled.json()
    assert (await ctx.client.post(url, json={"conv_id": identity.conv_id})).json() == data
    assert len((await ctx.client.get(f"/visits/{identity.conv_id}/messages")).json()["items"]) == 4
    assert not ctx.orch.recognize_intent.called and not ctx.memory._client.calls


@pytest.mark.asyncio
async def test_confirmation_errors_keep_business_state_unchanged(wired_api):
    from datetime import timedelta
    ctx = wired_api
    identity, proposal = await prepare_for_api(ctx)
    other, _ = await prepare_for_api(ctx, "patient_self")
    url = f"/appointment-proposals/{proposal['proposal_id']}/confirm"
    wrong = await ctx.client.post(url, json={"conv_id": other.conv_id})
    assert wrong.status_code == 403 and wrong.json()["detail"]["code"] == "identity_conflict"
    assert (await ctx.client.post(url, json={"conv_id": identity.conv_id, "patient_id": "patient_self"})).status_code == 422
    replacement = await ctx.service.prepare_appointment(identity, slot_id=proposal["target_id"])
    old = await ctx.client.post(url, json={"conv_id": identity.conv_id})
    assert old.status_code == 409 and old.json()["detail"]["code"] == "proposal_superseded"
    ctx.clock.value += timedelta(minutes=15)
    expired = await ctx.client.post(f"/appointment-proposals/{replacement.data['proposal_id']}/confirm", json={"conv_id": identity.conv_id})
    assert expired.status_code == 410 and expired.json()["detail"]["code"] == "proposal_expired"
    assert (await ctx.service.get_appointments(identity)).data["items"] == []


@pytest.mark.asyncio
async def test_confirmation_retries_history_write_after_success_without_booking_twice(wired_api, monkeypatch):
    from memory.visit_store import VisitStoreError
    ctx = wired_api
    identity, proposal = await prepare_for_api(ctx)
    append = ctx.visits.append_messages
    failed = AsyncMock(side_effect=VisitStoreError("storage_unavailable", "历史暂不可用", retryable=True))
    monkeypatch.setattr(ctx.visits, "append_messages", failed)
    url = f"/appointment-proposals/{proposal['proposal_id']}/confirm"
    response = await ctx.client.post(url, json={"conv_id": identity.conv_id})
    assert response.status_code == 503 and response.json()["detail"]["retryable"]
    records = (await ctx.service.get_appointments(identity)).data["items"]
    assert len(records) == 1
    monkeypatch.setattr(ctx.visits, "append_messages", append)
    retried = await ctx.client.post(url, json={"conv_id": identity.conv_id})
    assert retried.status_code == 200
    assert retried.json()["receipt"]["appointment"]["appointment_id"] == records[0]["appointment_id"]
    assert len(await ctx.visits.get_messages(identity.user_id, identity.conv_id)) == 2


@pytest.mark.asyncio
async def test_skill_reload_changes_next_chat_prompt(wired_api, monkeypatch, tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: 演示资料\nagents: general\nenabled: true\n---\n第一版到院说明", encoding="utf-8")
    skills = SkillManager(str(tmp_path))
    skills.load()
    monkeypatch.setattr(main, "_skill_manager", skills)
    wired_api.orch.set_skill_manager(skills)
    model = wired_api.orch._pool[AgentType.GENERAL][0]._client
    assert (await wired_api.client.post("/chat", json={"message": "医院信息"})).status_code == 200
    assert "第一版到院说明" in model.calls[-1]["system"]
    path.write_text(path.read_text(encoding="utf-8").replace("第一版到院说明", "第二版到院说明"), encoding="utf-8")
    assert (await wired_api.client.post("/skills/reload")).status_code == 200
    assert (await wired_api.client.post("/chat", json={"message": "医院信息"})).status_code == 200
    assert "第二版到院说明" in model.calls[-1]["system"] and "第一版到院说明" not in model.calls[-1]["system"]


@pytest.mark.asyncio
async def test_search_preserves_sources_and_failure_status(api_client, monkeypatch):
    from mcp.tool_manager import ToolResult
    search = AsyncMock(return_value=ToolResult(True, [{"title": "材料", "source_id": "prep", "source": "knowledge/preparation.md"}], "knowledge_search"))
    monkeypatch.setattr(main, "_tool_manager", SimpleNamespace(search_with_rewrite=search))
    result = (await api_client.client.post("/search", params={"query": "材料"})).json()
    assert result["success"] and result["results"][0]["source_id"] == "prep"
    search.return_value = ToolResult(False, [], "knowledge_search", error="检索不可用", fallback_used=True, error_code="recall_failed")
    failed = (await api_client.client.post("/search", params={"query": "材料"})).json()
    assert not failed["success"] and failed["results"] == [] and failed["fallback_used"]
    assert failed["error_code"] == "recall_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["target_unavailable", "conflict"])
async def test_confirmation_inventory_and_watch_conflicts_map_to_http_without_partial_writes(wired_api, failure):
    ctx = wired_api
    identity, proposal = await prepare_for_api(ctx)
    slot = await ctx.service.store.get_slot(proposal["target_id"])
    if failure == "target_unavailable":
        slot = slot.model_copy(update={"remaining": 0})
        await ctx.redis.set(ctx.service.store.slot_key(slot.slot_id), slot.model_dump_json())
    else:
        async def concurrent_update():
            await ctx.redis.set(ctx.service.store.slot_key(slot.slot_id), slot.model_dump_json())
        ctx.redis.before_execute = concurrent_update
    response = await ctx.client.post(f"/appointment-proposals/{proposal['proposal_id']}/confirm", json={"conv_id": identity.conv_id})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == failure
    assert response.json()["detail"]["retryable"] is (failure == "conflict")
    assert (await ctx.service.store.get_proposal(proposal["proposal_id"])).status == "pending"
    assert await ctx.service.store.get_slot(slot.slot_id) == slot
    assert (await ctx.service.get_appointments(identity)).data["items"] == []
    assert await ctx.visits.get_messages(identity.user_id, identity.conv_id) == []


@pytest.mark.asyncio
async def test_confirmation_rejects_other_visit_and_archived_owner(wired_api):
    ctx = wired_api
    identity, proposal = await prepare_for_api(ctx)
    other = (await ctx.client.post("/visits", json={"patient_id": identity.patient_id})).json()
    url = f"/appointment-proposals/{proposal['proposal_id']}/confirm"
    forbidden = await ctx.client.post(url, json={"conv_id": other["conv_id"]})
    assert forbidden.status_code == 403 and forbidden.json()["detail"]["code"] == "identity_conflict"
    assert (await ctx.client.patch(f"/visits/{identity.conv_id}", json={"archived": True})).status_code == 200
    archived = await ctx.client.post(url, json={"conv_id": identity.conv_id})
    assert archived.status_code == 409 and archived.json()["detail"]["code"] == "visit_archived"
    assert await ctx.service.store.get_appointments(identity.user_id, identity.patient_id) == []
    assert (await ctx.service.store.get_proposal(proposal["proposal_id"])).status == "pending"


@pytest.mark.asyncio
async def test_confirmation_retries_window_failure_without_duplicate_history(wired_api, monkeypatch):
    from redis.exceptions import ConnectionError
    ctx = wired_api
    identity, proposal = await prepare_for_api(ctx)
    invalidate = ctx.memory.invalidate_working_memory
    monkeypatch.setattr(ctx.memory, "invalidate_working_memory", AsyncMock(side_effect=ConnectionError("窗口失效暂不可用")))
    url = f"/appointment-proposals/{proposal['proposal_id']}/confirm"
    first = await ctx.client.post(url, json={"conv_id": identity.conv_id})
    assert first.status_code == 503 and first.json()["detail"]["retryable"]
    before = await ctx.visits.get_messages(identity.user_id, identity.conv_id)
    assert len(before) == 2
    monkeypatch.setattr(ctx.memory, "invalidate_working_memory", invalidate)
    retried = await ctx.client.post(url, json={"conv_id": identity.conv_id})
    assert retried.status_code == 200
    assert await ctx.visits.get_messages(identity.user_id, identity.conv_id) == before
    assert len((await ctx.service.get_appointments(identity)).data["items"]) == 1


@pytest.mark.asyncio
async def test_emergency_at_compression_threshold_still_calls_no_model(wired_api, monkeypatch):
    from memory.conversation_memory import MsgRole
    ctx = wired_api
    visit = await ctx.visits.create_visit("anonymous", "patient_child")
    identity = VisitIdentity(user_id=visit.user_id, patient_id=visit.patient_id, conv_id=visit.conv_id)
    for index in range(ctx.memory.COMPRESS_AT - 1):
        await ctx.memory.add_message(identity, MsgRole.USER, f"此前信息{index}", compress=False)
    forbidden = AsyncMock(side_effect=AssertionError("急症不应触发任何普通模型路径"))
    monkeypatch.setattr(ctx.memory, "get_context", forbidden)
    monkeypatch.setattr(ctx.memory, "update_profile", forbidden)
    monkeypatch.setattr(ctx.memory, "_compress", forbidden)
    ctx.memory._client.messages.create = forbidden
    ctx.orch.recognize_intent = forbidden
    for pool in ctx.orch._pool.values():
        for agent in pool:
            agent._client = SimpleNamespace(messages=SimpleNamespace(create=forbidden))
    response = await ctx.client.post("/chat", json={"conv_id": identity.conv_id, "message": "现在呼吸困难"})
    assert response.status_code == 200 and response.json()["intent"] == "emergency"
    assert forbidden.call_count == 0
    assert len(await ctx.memory._get_working_memory(identity)) == ctx.memory.COMPRESS_AT + 1
    assert len(await ctx.visits.get_messages(identity.user_id, identity.conv_id)) == 2
