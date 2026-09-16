import asyncio
import json
from collections import deque
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agents.agent_orchestrator import (
    AgentProfile,
    AgentResponse,
    AgentType,
    AgentOrchestrator,
    AppointmentAgent,
    EscalationAgent,
    GeneralAgent,
    Request,
    ResponseComposer,
    RoutingDecision,
    GuidanceAgent,
    build_shared_rag_tools,
)
from core.intent_recognizer import IntentCategory, UrgencyLevel
from core.emergency import EMERGENCY_RESPONSE, detect_emergency
from hospital.service import HospitalService
from hospital.store import HospitalStore
from hospital.models import Artifact, ServiceResult, Slot, VisitIdentity
from memory.visit_store import VisitStore
from mcp.tool_manager import ToolResult
from agents.tools import make_tool
from test_appointment_flow import BusinessRedis
from test_hospital_service import contract_example
from datetime import datetime, timedelta
import pytest_asyncio


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

        class Messages:
            async def create(inner, **kwargs):
                self.calls.append(kwargs)
                if self.error:
                    raise self.error
                return self.response

        self.messages = Messages()


def make_request(**kwargs):
    values = {
        "message": "查一下明天儿科的号，顺便告诉我要带什么",
        "user_id": "u1",
        "conv_id": "c1",
        "patient_id": "patient_child",
        "intent": IntentCategory.SLOT_QUERY,
        "intent_group": "appointment",
        "urgency": UrgencyLevel.HIGH,
        "intent_confidence": 0.92,
        "entities": {"department": ["儿科"], "date": ["2026-09-17"], "period": ["afternoon"]},
    }
    values.update(kwargs)
    return Request(**values)


def test_agent_profiles_have_distinct_contracts_and_generation_config():
    assert isinstance(GeneralAgent.profile, AgentProfile)
    assert GeneralAgent.profile.role != GuidanceAgent.profile.role
    assert GuidanceAgent.profile.workflow != AppointmentAgent.profile.workflow
    assert GuidanceAgent.profile.temperature < GeneralAgent.profile.temperature
    assert "search_knowledge_base" in GeneralAgent.profile.tool_scope
    assert "get_visit_checklist" in GuidanceAgent.profile.tool_scope
    assert "prepare_appointment" in AppointmentAgent.profile.tool_scope


def test_domain_agents_build_different_role_packets():
    req = make_request()
    general_packet = GeneralAgent(FakeClient(), "test-model")._build_role_packet(req)
    guidance_packet = GuidanceAgent(FakeClient(), "test-model")._build_role_packet(req)
    appointment_packet = AppointmentAgent(FakeClient(), "test-model")._build_role_packet(req)

    assert "triage_targets" in general_packet
    assert "guidance_fields" in guidance_packet
    assert "appointment_fields" in appointment_packet
    assert general_packet != guidance_packet != appointment_packet
    for packet in (general_packet, guidance_packet, appointment_packet):
        assert json.loads(packet)["visit_identity"] == {
            "user_id": "u1", "patient_id": "patient_child", "conv_id": "c1",
        }


def test_escalation_agent_is_a_real_non_llm_handoff_node():
    client = FakeClient()
    agent = EscalationAgent(client, "test-model")

    result = asyncio.run(agent.handle(make_request(
        intent=IntentCategory.HUMAN_HANDOFF,
        urgency=UrgencyLevel.CRITICAL,
    )))

    assert result.success is True
    assert result.escalate is True
    assert "人工导诊" in result.content
    assert "尚未向工作人员提交请求" in result.content
    assert client.calls == []


def test_composer_fallback_preserves_primary_and_supporting_results():
    composer = ResponseComposer(FakeClient(error=RuntimeError("provider down")), "test-model")
    req = make_request()
    responses = [
        AgentResponse(AgentType.APPOINTMENT, "请确认希望查询的日期。", True),
        AgentResponse(AgentType.GUIDANCE, "就诊请携带有效证件。", True),
    ]

    content = asyncio.run(composer.compose(req, responses))

    assert content.startswith("请确认希望查询的日期。")
    assert "补充说明" in content
    assert "有效证件" in content


def test_routing_decision_can_target_escalation_pool():
    # Keep this assertion close to the public data contract used by the API.
    decision = RoutingDecision(
        primary_agent=AgentType.ESCALATION,
        reason="critical request",
        confidence=1.0,
    )
    assert decision.agent_types == [AgentType.ESCALATION]
    assert not decision.multi_agent


def test_composite_request_routes_preparation_as_supporting_guidance_agent():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        AgentType.GENERAL: [object()],
        AgentType.GUIDANCE: [object()],
        AgentType.APPOINTMENT: [object()],
    }

    decision = orchestrator._route_decision(make_request())

    assert decision.primary_agent is AgentType.APPOINTMENT
    assert decision.supporting_agents == [AgentType.GUIDANCE]
    assert decision.multi_agent is True


def test_hospital_tool_scopes_are_isolated():
    general_tools = set(GeneralAgent(FakeClient(), "test-model").get_tools())
    guidance_tools = set(GuidanceAgent(FakeClient(), "test-model").get_tools())
    appointment_tools = set(AppointmentAgent(FakeClient(), "test-model").get_tools())
    escalation_tools = set(EscalationAgent(FakeClient(), "test-model").get_tools())

    assert general_tools == {"inspect_request_context", "query_hospital_catalog"}
    assert guidance_tools == {"get_visit_checklist", "get_wayfinding"}
    assert appointment_tools == {"search_slots", "list_appointments", "prepare_appointment", "prepare_cancellation"}
    assert escalation_tools == set()
    assert not general_tools & guidance_tools
    assert not guidance_tools & appointment_tools


def test_shared_rag_tool_is_available_to_query_roles_only():
    class RagManager:
        async def search_with_rewrite(self, tool_name, query, top_k=5):
            return type(
                "Result",
                (),
                {"success": True, "data": [{"title": "就诊准备", "content": "请携带有效证件"}], "reranked": True},
            )()

    shared = build_shared_rag_tools(RagManager())

    general = GeneralAgent(FakeClient(), "test-model")
    guidance = GuidanceAgent(FakeClient(), "test-model")
    appointment = AppointmentAgent(FakeClient(), "test-model")
    escalation = EscalationAgent(FakeClient(), "test-model")

    for agent in (general, guidance, appointment):
        agent.set_shared_tools(shared)
        tools = agent.get_tools()
        assert "search_knowledge_base" in tools
    escalation.set_shared_tools(shared)
    assert escalation.get_tools() == {}


def test_tool_input_validation_rejects_unknown_fields():
    agent = GuidanceAgent(FakeClient(), "test-model")
    spec = agent.get_tools()["get_visit_checklist"]

    try:
        agent._validate_tool_input(spec, {"visit_type": "first", "secret": "nope"})
    except ValueError as exc:
        assert "不允许的工具参数" in str(exc)
    else:
        raise AssertionError("unknown tool fields should be rejected")


def test_tool_use_round_trip_executes_only_whitelisted_tool():
    class ToolUseBlock:
        type = "tool_use"
        id = "toolu_1"
        name = "get_visit_checklist"
        input = {"visit_type": "first"}

    class TextBlock:
        type = "text"
        text = "已返回预置就诊材料清单。"

    class ToolClient:
        def __init__(self):
            self.calls = []
            self.responses = [
                type("Response", (), {"content": [ToolUseBlock()]})(),
                type("Response", (), {"content": [TextBlock()]})(),
            ]

        class Messages:
            def __init__(self, owner):
                self.owner = owner

            async def create(self, **kwargs):
                self.owner.calls.append(kwargs)
                return self.owner.responses.pop(0)

        @property
        def messages(self):
            return self.Messages(self)

    client = ToolClient()
    agent = GuidanceAgent(client, "test-model", hospital_service=HospitalService())
    response = asyncio.run(agent.handle(make_request()))

    assert response.success is True
    assert response.tools_used == ["get_visit_checklist"]
    assert len(client.calls) == 2
    assert {tool["name"] for tool in client.calls[0]["tools"]} == {
        "get_visit_checklist",
        "get_wayfinding",
    }
    assert "tool_result" in str(client.calls[1]["messages"])


def test_repeated_tool_requests_stop_after_three_model_calls():
    class RepeatingToolClient:
        def __init__(self):
            self.calls = []
            self.messages = self

        async def create(self, **kwargs):
            self.calls.append(deepcopy(kwargs))
            # Bound the fake too, so a broken production loop fails instead of hanging.
            if len(self.calls) > 3:
                raise AssertionError("unexpected fourth model request")
            return SimpleNamespace(content=[SimpleNamespace(
                type="tool_use",
                id=f"toolu_{len(self.calls)}",
                name="get_visit_checklist",
                input={"visit_type": "first"},
            )])

    client = RepeatingToolClient()
    agent = GuidanceAgent(client, "test-model", hospital_service=HospitalService())

    response = asyncio.run(agent.handle(make_request()))

    assert len(client.calls) == 3
    assert response.success is False
    assert "处理您的请求时出现问题" in response.content
    for call_number, call in enumerate(client.calls[1:], start=2):
        previous_result = call["messages"][-1]["content"][0]
        assert previous_result["type"] == "tool_result"
        assert previous_result["tool_use_id"] == f"toolu_{call_number - 1}"


@pytest_asyncio.fixture
async def hospital_tools_context():
    redis = BusinessRedis()
    clock = SimpleNamespace(value=datetime.fromisoformat("2026-09-16T10:00:00+08:00"))
    visits = VisitStore(redis, prefix="medipet:test:a03:", clock=lambda: clock.value)
    service = HospitalService(store=HospitalStore(redis, visits.prefix), visit_store=visits, clock=lambda: clock.value)
    await visits.initialize_patients(service.data.patients)
    requests = {}
    for patient in ("patient_child", "patient_self"):
        visit = await visits.create_visit("anonymous", patient)
        requests[patient] = make_request(user_id="anonymous", patient_id=patient, conv_id=visit.conv_id)
    agent = AppointmentAgent(FakeClient(), "test-model", hospital_service=service, visit_store=visits)
    return SimpleNamespace(service=service, visits=visits, agent=agent, requests=requests, clock=clock, redis=redis)


async def invoke_tool(agent, name, req, **args):
    spec = agent.get_tools()[name]
    agent._validate_tool_input(spec, args)
    result = spec.handler(req, args)
    import inspect
    return await result if inspect.isawaitable(result) else result


@pytest.mark.asyncio
async def test_catalog_checklist_wayfinding_tools_match_real_service():
    service = HospitalService()
    general = GeneralAgent(FakeClient(), "test-model", hospital_service=service)
    guidance = GuidanceAgent(FakeClient(), "test-model", hospital_service=service)
    cases = [
        (general, "query_hospital_catalog", {"category": "doctor", "department": "儿科"}),
        (guidance, "get_visit_checklist", {"department": "儿科", "visit_type": "child"}),
        (guidance, "get_wayfinding", {"origin": "hall", "destination": "pediatrics-room", "mode": "accessible"}),
        (guidance, "get_wayfinding", {"origin": "未知地点", "destination": "儿科诊区"}),
    ]
    for agent, name, args in cases:
        actual = await invoke_tool(agent, name, make_request(), **args)
        expected = getattr(service, name)(**args).model_dump(mode="json")
        # 每次静态查询独立生成展示卡 ID；其余业务事实必须一致。
        for payload in (actual, expected):
            for artifact in payload["artifacts"]:
                artifact.pop("id")
        assert actual == expected
        if args.get("origin") != "未知地点":
            assert actual["success"]
    snapshot = await invoke_tool(general, "inspect_request_context", make_request())
    assert snapshot["data"]["entities"]["department"] == ["儿科"]


@pytest.mark.asyncio
async def test_slots_selection_proposal_and_chat_confirmation_never_book(hospital_tools_context):
    ctx = hospital_tools_context
    req = ctx.requests["patient_child"]
    found = await invoke_tool(ctx.agent, "search_slots", req, department="儿科", date="明天")
    assert found["success"] and found["artifacts"][0]["type"] == "slot_list"
    state = await ctx.visits.get_selection(req.user_id, req.conv_id)
    assert [slot.slot_id for slot in state.slots] == [slot["slot_id"] for slot in found["data"]["slots"]]
    prepared = await invoke_tool(ctx.agent, "prepare_appointment", req, selection_index=1)
    assert prepared["success"] and prepared["data"]["target_id"] == state.slots[0].slot_id
    assert prepared["data"]["status"] == "pending"
    repeated = await invoke_tool(ctx.agent, "prepare_appointment", replace(req, message="确认预约"))
    assert repeated == prepared
    assert (await invoke_tool(ctx.agent, "list_appointments", req))["data"] == {"items": []}
    assert (await ctx.service.store.get_slot(state.slots[0].slot_id)).remaining == state.slots[0].remaining
    assert not {"confirm_proposal", "create_appointment", "cancel_appointment"} & ctx.agent.get_tools().keys()


@pytest.mark.asyncio
async def test_new_empty_and_failed_queries_clear_old_selection_in_current_visit(hospital_tools_context):
    ctx = hospital_tools_context
    child, other = ctx.requests.values()
    for req in (child, other):
        await invoke_tool(ctx.agent, "search_slots", req, department="儿科", date="明天")
    first = await ctx.visits.get_selection(child.user_id, child.conv_id)
    changed = await invoke_tool(ctx.agent, "search_slots", child, department="眼科", date="后天")
    chosen = await invoke_tool(ctx.agent, "prepare_appointment", child, selection_index=1)
    assert chosen["data"]["target_id"] == changed["data"]["slots"][0]["slot_id"]
    assert chosen["data"]["target_id"] != first.slots[0].slot_id
    empty = await invoke_tool(ctx.agent, "search_slots", child, date="2030-01-01")
    assert empty["error_code"] == "no_slots"
    empty_state = await ctx.visits.get_selection(child.user_id, child.conv_id)
    assert empty_state.status == "empty" and empty_state.slots == [] and empty_state.list_id is None
    assert empty_state.query.model_dump(mode="json") == empty["data"]["query"]
    assert empty_state.current_proposal_id == chosen["data"]["proposal_id"]
    assert (await invoke_tool(ctx.agent, "prepare_appointment", child, selection_index=1))["error_code"] == "selection_unavailable"
    await invoke_tool(ctx.agent, "search_slots", child, department="儿科", date="明天")
    previous_query = (await ctx.visits.get_selection(child.user_id, child.conv_id)).query
    failed = await invoke_tool(ctx.agent, "search_slots", child, department="未知科室")
    assert failed["error_code"] == "not_found"
    failed_state = await ctx.visits.get_selection(child.user_id, child.conv_id)
    assert failed_state.status == "failed" and failed_state.query == previous_query
    assert failed_state.slots == [] and failed_state.list_id is None
    assert (await invoke_tool(ctx.agent, "prepare_appointment", child, selection_index=1))["error_code"] == "selection_unavailable"
    assert (await ctx.visits.get_selection(other.user_id, other.conv_id)).status == "ready"


@pytest.mark.asyncio
async def test_dynamic_queries_bypass_shared_cache_and_respect_patient_identity(hospital_tools_context):
    ctx = hospital_tools_context
    child, other = ctx.requests.values()
    found = await invoke_tool(ctx.agent, "search_slots", child, department="儿科", date="明天")
    slot = Slot.model_validate(found["data"]["slots"][0])
    await ctx.redis.set(ctx.service.store.slot_key(slot.slot_id), slot.model_copy(update={"remaining": 1}).model_dump_json())
    fresh = await invoke_tool(ctx.agent, "search_slots", child, department="儿科", date="明天")
    assert fresh["data"]["slots"][0]["remaining"] == 1
    proposal = await invoke_tool(ctx.agent, "prepare_appointment", child, selection_index=1)
    identity = VisitIdentity(user_id=child.user_id, patient_id=child.patient_id, conv_id=child.conv_id)
    # 测试固定业务记录；确认方法始终不向模型开放。
    confirmed = await ctx.service.confirm_proposal(identity, proposal["data"]["proposal_id"])
    assert confirmed.success
    mine = await invoke_tool(ctx.agent, "list_appointments", child)
    assert len(mine["data"]["items"]) == 1
    assert (await invoke_tool(ctx.agent, "list_appointments", other))["data"] == {"items": []}
    forged = await invoke_tool(ctx.agent, "list_appointments", replace(child, patient_id=other.patient_id))
    assert forged["error_code"] == "identity_conflict"
    appointment_id = mine["data"]["items"][0]["appointment_id"]
    cancellation = await invoke_tool(ctx.agent, "prepare_cancellation", child, appointment_id=appointment_id)
    assert cancellation["success"] and cancellation["data"]["operation"] == "cancel"
    assert (await invoke_tool(ctx.agent, "list_appointments", child))["data"] == mine["data"]
    assert (await ctx.service.store.get_slot(slot.slot_id)).remaining == 0
    assert (await invoke_tool(ctx.agent, "prepare_cancellation", other, appointment_id=appointment_id))["error_code"] == "identity_conflict"


@pytest.mark.asyncio
async def test_expired_proposal_and_missing_service_preserve_error_categories(hospital_tools_context):
    ctx = hospital_tools_context
    req = ctx.requests["patient_child"]
    await invoke_tool(ctx.agent, "search_slots", req, department="儿科", date="明天")
    await invoke_tool(ctx.agent, "prepare_appointment", req, selection_index=1)
    ctx.clock.value += timedelta(minutes=15)
    assert (await invoke_tool(ctx.agent, "prepare_appointment", req))["error_code"] == "proposal_expired"
    unbound = AppointmentAgent(FakeClient(), "test-model")
    assert (await invoke_tool(unbound, "list_appointments", req))["error_code"] == "storage_unavailable"
    assert (await invoke_tool(ctx.agent, "list_appointments", replace(req, patient_id=None)))["error_code"] == "missing_fields"


@pytest.mark.asyncio
async def test_failed_slot_recall_leaves_no_reusable_selection(hospital_tools_context):
    ctx = hospital_tools_context
    req = ctx.requests["patient_child"]
    await invoke_tool(ctx.agent, "search_slots", req, date="明天")
    async def unavailable(**kwargs):
        return ServiceResult(success=False, error_code="storage_unavailable", error="测试号源存储不可用", retryable=True)
    ctx.service.search_slots = unavailable
    failed = await invoke_tool(ctx.agent, "search_slots", req, date="明天")
    assert failed["error_code"] == "storage_unavailable" and failed["retryable"]
    assert (await ctx.visits.get_selection(req.user_id, req.conv_id)).status == "failed"
    assert (await invoke_tool(ctx.agent, "prepare_appointment", req, selection_index=1))["error_code"] == "selection_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("args,expected", [
    ({"department": "儿科", "doctor": "许知宁", "date": "后天", "period": "afternoon"},
     {"department_id": "dep_pediatrics", "doctor_id": "doctor_xu", "date": "2026-09-18", "period": "afternoon"}),
    ({"department": "dep_pediatrics", "doctor": "doctor_xu", "date": "2026-09-18", "period": "morning"},
     {"department_id": "dep_pediatrics", "doctor_id": "doctor_xu", "date": "2026-09-18", "period": "morning"}),
    ({"date": "后天"}, {"department_id": None, "doctor_id": None, "date": "2026-09-18", "period": None}),
])
async def test_failed_slot_query_keeps_normalized_current_filters_and_proposal(hospital_tools_context, args, expected):
    ctx = hospital_tools_context
    req = ctx.requests["patient_child"]
    await invoke_tool(ctx.agent, "search_slots", req, department="儿科", date="明天")
    prepared = await invoke_tool(ctx.agent, "prepare_appointment", req, selection_index=1)
    async def unavailable(**kwargs):
        return ServiceResult(success=False, error_code="storage_unavailable", error="测试号源存储不可用", retryable=True)
    ctx.service.search_slots = unavailable
    failed = await invoke_tool(ctx.agent, "search_slots", req, **args)
    state = await ctx.visits.get_selection(req.user_id, req.conv_id)
    assert failed["error_code"] == "storage_unavailable"
    assert state.status == "failed" and state.list_id is None and state.slots == []
    assert state.query.model_dump(mode="json") == expected
    assert state.current_proposal_id == prepared["data"]["proposal_id"]
    assert (await invoke_tool(ctx.agent, "prepare_appointment", req, selection_index=1))["error_code"] == "selection_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("args,error_code", [
    ({"department": "未知科室"}, "not_found"),
    ({"doctor": "未知医生"}, "not_found"),
    ({"date": "不合法日期"}, "invalid_input"),
])
async def test_unresolvable_query_keeps_previous_filters_without_selectable_ids(hospital_tools_context, args, error_code):
    ctx = hospital_tools_context
    req = ctx.requests["patient_child"]
    await invoke_tool(ctx.agent, "search_slots", req, department="儿科", doctor="许知宁", date="明天", period="afternoon")
    prepared = await invoke_tool(ctx.agent, "prepare_appointment", req, selection_index=1)
    before = await ctx.visits.get_selection(req.user_id, req.conv_id)
    failed = await invoke_tool(ctx.agent, "search_slots", req, **args)
    state = await ctx.visits.get_selection(req.user_id, req.conv_id)
    assert failed["error_code"] == error_code
    assert state.status == "failed" and state.query == before.query
    assert state.slots == [] and state.list_id is None
    assert state.current_proposal_id == prepared["data"]["proposal_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role,tool,args", [
    (GeneralAgent, "query_hospital_catalog", {"category": "hospital"}),
    (GuidanceAgent, "get_visit_checklist", {"visit_type": "child"}),
    (AppointmentAgent, "search_slots", {"department": "儿科", "date": "明天"}),
])
async def test_model_round_trip_gets_real_hospital_tool_result(hospital_tools_context, role, tool, args):
    ctx = hospital_tools_context
    class Client:
        def __init__(self):
            self.messages = self
            self.calls = []
        async def create(self, **kwargs):
            self.calls.append(deepcopy(kwargs))
            if len(self.calls) == 1:
                return SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="real-tool", name=tool, input=args)])
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="请查看本轮业务资料。")])
    client = Client()
    agent = role(client, "test-model", hospital_service=ctx.service, visit_store=ctx.visits)
    response = await agent.handle(ctx.requests["patient_child"])
    assert response.success and response.tools_used == [tool] and len(client.calls) == 2
    result = json.loads(client.calls[1]["messages"][-1]["content"][0]["content"])
    assert result["success"] and result["artifacts"]


@pytest.mark.asyncio
async def test_model_failure_still_falls_back_to_general_agent():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        AgentType.GUIDANCE: [GuidanceAgent(FakeClient(error=RuntimeError("模型失败")), "test")],
        AgentType.GENERAL: [GeneralAgent(FakeClient(response=SimpleNamespace(content=[SimpleNamespace(type="text", text="请补充信息。")])) , "test")],
    }
    result = await orchestrator._execute(make_request(), AgentType.GUIDANCE)
    assert result.success and result.agent_type is AgentType.GENERAL and result.content == "请补充信息。"


@pytest.mark.parametrize("tool,args", [
    ("list_appointments", {"patient_id": "patient_self"}),
    ("search_slots", {"user_id": "somebody"}),
    ("prepare_appointment", {"conv_id": "other"}),
    ("prepare_appointment", {"selection_index": True}),
    ("prepare_appointment", {"selection_index": 0}),
    ("search_slots", {"period": "midnight"}),
    ("prepare_cancellation", {}),
])
def test_model_input_cannot_override_identity_or_use_invalid_selection(tool, args):
    agent = AppointmentAgent(FakeClient(), "test-model")
    with pytest.raises(ValueError):
        agent._validate_tool_input(agent.get_tools()[tool], args)


def test_inspect_context_accepts_no_model_parameters():
    agent = GeneralAgent(FakeClient(), "test-model")
    with pytest.raises(ValueError):
        agent._validate_tool_input(agent.get_tools()["inspect_request_context"], {"patient_id": "other"})


@pytest.mark.asyncio
async def test_non_whitelisted_tool_and_identity_override_do_not_reach_service():
    calls = []
    async def forbidden(req, args):
        calls.append(args)
    class Client:
        def __init__(self):
            self.messages = self
            self.calls = []
        async def create(self, **kwargs):
            self.calls.append(deepcopy(kwargs))
            if len(self.calls) == 1:
                return SimpleNamespace(content=[
                    SimpleNamespace(type="tool_use", id="bad1", name="confirm_proposal", input={}),
                    SimpleNamespace(type="tool_use", id="bad2", name="query_hospital_catalog", input={"patient_id": "other"}),
                ])
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="请核对当前事项。")])
    client = Client()
    service = SimpleNamespace(query_hospital_catalog=forbidden)
    agent = GeneralAgent(client, "test-model", hospital_service=service)
    agent.set_shared_tools({"confirm_proposal": make_tool("confirm_proposal", "不可开放", {}, forbidden)})
    result = await agent.handle(make_request())
    assert result.success and not calls and not result.tools_used
    assert "confirm_proposal" not in agent.get_tools()
    returned = client.calls[1]["messages"][-1]["content"]
    assert all(json.loads(row["content"])["success"] is False for row in returned)


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_shared_rag_preserves_k02_sources_and_failure_flags(success):
    source = {"chunk_id": "arrival:0", "doc_id": "arrival", "source": "knowledge/arrival-checkin.md", "source_id": "arrival", "content": "先报到"}
    expected = ToolResult(success, [source] if success else [], "knowledge_search", error=None if success else "召回故障",
                          error_code=None if success else "retrieval_failed", rewrite_error="改写失败", rerank_error="重排失败",
                          recall_errors=[{"query": "另一查询", "error": "失败"}], partial=success)
    class Manager:
        async def search_with_rewrite(self, *args, **kwargs):
            return expected
    agent = GeneralAgent(FakeClient(), "test-model")
    agent.set_shared_tools(build_shared_rag_tools(Manager()))
    result = await invoke_tool(agent, "search_knowledge_base", make_request(), query="报到")
    assert result["results"] == expected.data and result["success"] is success
    assert result["rewrite_error"] and result["rerank_error"] and result["recall_errors"]
    assert not result["reranked"] and result["error_code"] == expected.error_code


class SequenceClient:
    def __init__(self, *responses):
        self.messages = self
        self.pending = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        result = self.pending.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(content=result)


def text_blocks(text="请核对本次资料。"):
    return [SimpleNamespace(type="text", text=text)]


def tool_blocks(name="query_hospital_catalog", args=None):
    return [SimpleNamespace(type="tool_use", id="tool-result", name=name, input=args or {})]


def artifact_example(kind="contact_info", suffix=""):
    result = next(item for item in contract_example("artifact-examples") if item["type"] == kind)
    result["id"] += suffix
    return Artifact.model_validate(result)


def install_test_tool(agent, name, handler):
    agent._hospital_tools[name] = make_tool(name, "业务替身", {}, handler)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "exception"])
async def test_actual_rag_handler_sources_reach_request_trace_without_full_text_or_invention(outcome):
    source = {"title": "门诊报到", "source": "knowledge/arrival-checkin.md", "source_id": "arrival",
              "doc_id": "arrival-doc", "chunk_id": "arrival:0", "content": "正文不复制到轨迹" * 100, "score": .92}
    class Manager:
        async def search_with_rewrite(self, tool_name, query, top_k=5):
            assert tool_name == "knowledge_search" and query == "报到"
            if outcome == "exception":
                raise RuntimeError("检索服务故障")
            return ToolResult(outcome == "success", [source, {"title": "仅有标题", "content": "不能猜来源ID"}],
                              "knowledge_search", error=None if outcome == "success" else "未完成检索",
                              error_code=None if outcome == "success" else "retrieval_failed")
    client = SequenceClient(tool_blocks("search_knowledge_base", {"query": "报到"}), text_blocks())
    agent = GeneralAgent(client, "test-model")
    agent.set_shared_tools(build_shared_rag_tools(Manager()))
    response = await agent.handle(make_request())
    assert response.success and len(client.calls) == 2
    trace = response.tool_traces[0]
    assert trace["tool_name"] == "search_knowledge_base"
    if outcome == "success":
        expected = {key: source[key] for key in ("title", "source", "source_id", "doc_id", "chunk_id")}
        assert trace["sources"] == [expected, {"title": "仅有标题"}]
        assert trace["result_summary"]["result_count"] == 2 and trace["success"]
        source["title"] = "调用后修改原返回对象"
        assert trace["sources"][0]["title"] == "门诊报到"
        assert all("content" not in item and "score" not in item for item in trace["sources"])
    else:
        assert trace["sources"] == [] and not trace["success"]
        assert trace["result_summary"]["result_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["catalog", "slot_list", "appointment_proposal", "appointment_record", "visit_checklist", "wayfinding", "contact_info"])
async def test_tool_artifact_snapshot_and_success_trace_survive_text_return(kind):
    artifact = artifact_example(kind)
    expected = artifact.model_dump(mode="json")
    client = SequenceClient(tool_blocks(), text_blocks("模型不能改写业务资料"))
    agent = GeneralAgent(client, "test-model")
    install_test_tool(agent, "query_hospital_catalog", lambda req, args: {"success": True, "data": artifact.data, "artifacts": [expected]})
    response = await agent.handle(make_request())
    assert response.artifacts[0].model_dump(mode="json") == expected
    trace = response.tool_traces[0]
    assert len(response.tool_traces) == 1 and trace["success"] and trace["call_success"]
    assert trace["tool_name"] == "query_hospital_catalog" and trace["input"] == {}
    assert trace["result_summary"]["artifact_ids"] == [artifact.id]
    assert "sources" not in trace
    # 工具服务继续持有的 dict 不得改变已返回快照。
    expected["data"].clear()
    assert response.artifacts[0].data


@pytest.mark.asyncio
async def test_empty_or_fabricated_model_text_never_creates_business_artifacts():
    agent = GeneralAgent(SequenceClient(text_blocks("已创建预约，编号 invented-123")), "test")
    result = await agent.handle(make_request())
    assert result.artifacts == [] and result.tool_traces == [] and result.tools_used == []
    assert not hasattr(agent, "_last_tools_used") and not hasattr(agent, "_last_tool_traces")


@pytest.mark.asyncio
async def test_model_failure_and_general_fallback_preserve_prior_artifacts():
    artifact = artifact_example("appointment_proposal")
    primary = AppointmentAgent(SequenceClient(tool_blocks("prepare_appointment"), RuntimeError("答复失败")), "test")
    install_test_tool(primary, "prepare_appointment", lambda req, args: {"success": True, "artifacts": [artifact.model_dump(mode="json")]})
    fallback = GeneralAgent(SequenceClient(text_blocks("已保留资料，请核对。")), "test")
    orchestrator = make_orchestrator()
    orchestrator._pool[AgentType.APPOINTMENT] = [primary]
    orchestrator._pool[AgentType.GENERAL] = [fallback]
    result = await orchestrator.run(make_request(message="选择第一个", intent=IntentCategory.APPOINTMENT_CREATE, entities={}))
    assert result.artifacts == [artifact] and result.agent_type is AgentType.GENERAL
    assert result.tools_used == ["prepare_appointment"]
    assert any(t.get("kind") == "agent_execution" and not t["success"] and t["agent_type"] == "appointment" for t in result.tool_traces)
    assert orchestrator.get_tool_trace(result.request_id)["tool_calls"] == result.tool_traces


@pytest.mark.asyncio
async def test_concurrent_and_later_turns_on_one_agent_do_not_share_patient_results():
    entered = set()
    both_entered = asyncio.Event()
    class ConcurrentClient:
        messages = None
        def __init__(self):
            self.messages = self
        async def create(self, **kwargs):
            message = kwargs["messages"][-1]["content"]
            if isinstance(message, str):
                return SimpleNamespace(content=tool_blocks())
            content = json.loads(message[0]["content"])
            name = content["artifacts"][0]["data"]["summary"]
            entered.add(name)
            if len(entered) == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), 1)
            if name == "child":
                raise RuntimeError("仅孩子请求模型失败")
            return SimpleNamespace(content=text_blocks("本人资料"))
    agent = GeneralAgent(ConcurrentClient(), "test")
    def tool(req, args):
        artifact = artifact_example(suffix=req.conv_id)
        artifact.data["summary"] = req.message
        return {"success": True, "artifacts": [artifact.model_dump(mode="json")]}
    install_test_tool(agent, "query_hospital_catalog", tool)
    child, own = await asyncio.gather(agent.handle(make_request(message="child", conv_id="child")),
                                     agent.handle(make_request(message="self", conv_id="self", patient_id="patient_self")))
    assert not child.success and own.success
    assert [a.data["summary"] for a in child.artifacts] == ["child"]
    assert [a.data["summary"] for a in own.artifacts] == ["self"]
    assert len(child.tool_traces) == 2 and len(own.tool_traces) == 1
    agent._client = SequenceClient(text_blocks("新的普通答复"))
    later = await agent.handle(make_request())
    assert later.artifacts == [] and later.tool_traces == [] and later.tools_used == []


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", [False, True])
async def test_parallel_dedup_conflict_and_composer_failure_preserve_other_cards(conflict):
    common = artifact_example()
    changed = common.model_copy(deep=True)
    if conflict:
        changed.data["summary"] = "冲突版本"
    first_only = artifact_example("visit_checklist")
    second_only = artifact_example("wayfinding")
    orchestrator = make_orchestrator()
    original = [a.model_dump(mode="json") for a in (common, changed, first_only, second_only)]
    async def execute(req, role):
        return AgentResponse(role, role.value + "原始结果", True,
                             artifacts=[common, first_only] if role is AgentType.APPOINTMENT else [changed, second_only])
    orchestrator._execute = execute
    result = await orchestrator.run_parallel(make_request(), RoutingDecision(AgentType.APPOINTMENT, [AgentType.GUIDANCE]))
    assert {a.id for a in result.artifacts} == ({first_only.id, second_only.id} if conflict else {common.id, first_only.id, second_only.id})
    assert any(t.get("error_code") == "artifact_conflict" for t in result.tool_traces) is conflict
    assert "appointment原始结果" in result.response and "guidance原始结果" in result.response
    assert original == [a.model_dump(mode="json") for a in (common, changed, first_only, second_only)]


@pytest.mark.asyncio
async def test_within_turn_conflict_never_revives_in_parallel_merge():
    common = artifact_example()
    other = common.model_copy(deep=True)
    other.data["summary"] = "不同快照"
    agent = AppointmentAgent(SequenceClient(tool_blocks("prepare_appointment"), text_blocks()), "test")
    install_test_tool(agent, "prepare_appointment", lambda req, args: {
        "success": True, "artifacts": [a.model_dump(mode="json") for a in (common, other, common)]})
    conflicted = await agent.handle(make_request())
    assert not conflicted.artifacts and len([t for t in conflicted.tool_traces if t.get("error_code") == "artifact_conflict"]) == 1
    orchestrator = make_orchestrator()
    async def execute(req, role):
        return conflicted if role is AgentType.APPOINTMENT else AgentResponse(role, "资料", True, artifacts=[common])
    orchestrator._execute = execute
    result = await orchestrator.run_parallel(make_request(), RoutingDecision(AgentType.APPOINTMENT, [AgentType.GUIDANCE]))
    assert result.artifacts == []


@pytest.mark.asyncio
async def test_business_failure_keeps_empty_slot_card_and_failed_trace(hospital_tools_context):
    ctx = hospital_tools_context
    client = SequenceClient(tool_blocks("search_slots", {"date": "2030-01-01"}), text_blocks("没有可用号源。"))
    agent = AppointmentAgent(client, "test", hospital_service=ctx.service, visit_store=ctx.visits)
    result = await agent.handle(ctx.requests["patient_child"])
    assert result.success and result.artifacts[0].type == "slot_list"
    assert result.artifacts[0].data["slots"] == []
    trace = result.tool_traces[0]
    assert trace["call_success"] and not trace["success"] and not trace["result_success"]
    assert trace["error_code"] == "no_slots"


@pytest.mark.asyncio
async def test_parallel_failed_task_keeps_valid_prior_artifact_and_failure_evidence():
    artifact = artifact_example("appointment_proposal")
    orchestrator = make_orchestrator()
    async def execute(req, role):
        if role is AgentType.APPOINTMENT:
            return AgentResponse(role, "模型失败", False, artifacts=[artifact], tool_traces=[{"kind": "agent_execution", "agent_type": role.value, "success": False}])
        return AgentResponse(role, "请带证件。", True)
    orchestrator._execute = execute
    result = await orchestrator.run_parallel(make_request(), RoutingDecision(AgentType.APPOINTMENT, [AgentType.GUIDANCE]))
    assert result.artifacts == [artifact] and result.response == "请带证件。"
    assert result.agent_types == [AgentType.GUIDANCE]
    assert result.tool_traces[0]["success"] is False


@pytest.mark.asyncio
async def test_unexpected_composer_exception_still_returns_task_data():
    artifact = artifact_example()
    orchestrator = make_orchestrator()
    async def execute(req, role):
        return AgentResponse(role, role.value + "结果", True, artifacts=[artifact])
    async def compose(*args):
        raise RuntimeError("整合节点异常")
    orchestrator._execute = execute
    orchestrator._composer.compose = compose
    result = await orchestrator.run_parallel(make_request(), RoutingDecision(AgentType.APPOINTMENT, [AgentType.GUIDANCE]))
    assert result.artifacts == [artifact]
    assert "appointment结果" in result.response and "guidance结果" in result.response
    assert any(trace.get("kind") == "composition" and not trace["success"] for trace in result.tool_traces)


@pytest.mark.asyncio
async def test_non_object_tool_input_is_preserved_in_failure_trace():
    client = SequenceClient(tool_blocks(args="bad input"), text_blocks())
    response = await GeneralAgent(client, "test").handle(make_request())
    assert response.success and not response.tool_traces[0]["success"]
    assert response.tool_traces[0]["input"] == "bad input"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["现在呼吸困难", "有人突然失去意识", "现在剧烈胸痛"])
@pytest.mark.parametrize("preclassified", [True, False])
async def test_emergency_entry_skips_recognizer_model_and_ordinary_dispatch(message, preclassified):
    forbidden = FakeClient(error=AssertionError("不得调用模型"))
    orchestrator = make_orchestrator(general=forbidden, guidance=forbidden, appointment=forbidden, escalation=forbidden)
    async def fail(*args, **kwargs):
        raise AssertionError("不得运行普通流程")
    def fail_sync(*args, **kwargs):
        raise AssertionError("不得路由普通角色")
    orchestrator._intent_recognizer = SimpleNamespace(recognize=fail)
    orchestrator._execute = fail
    orchestrator.run_parallel = fail
    orchestrator._route_decision = fail_sync
    req = make_request(message=message, intent=IntentCategory.APPOINTMENT_CREATE if preclassified else None)
    before = deepcopy(req)
    result = await orchestrator.run(req)
    assert result.intent is IntentCategory.EMERGENCY and result.agent_type is AgentType.ESCALATION
    assert EMERGENCY_RESPONSE in result.response
    assert result.artifacts[0].type == "contact_info" and result.artifacts[0].data["delivery"] == "contact_only"
    assert not result.tools_used and not result.tool_traces and not forbidden.calls
    assert req == before
    assert orchestrator.get_tool_trace(req.request_id)["tool_calls"] == []


@pytest.mark.asyncio
async def test_emergency_preserves_pending_proposal_patient_and_inventory(hospital_tools_context):
    ctx = hospital_tools_context
    req = ctx.requests["patient_child"]
    await invoke_tool(ctx.agent, "search_slots", req, department="儿科", date="明天")
    proposal = await invoke_tool(ctx.agent, "prepare_appointment", req, selection_index=1)
    before_strings, before_hashes = deepcopy(ctx.redis.strings), deepcopy(ctx.redis.hashes)
    orchestrator = make_orchestrator()
    orchestrator._pool[AgentType.APPOINTMENT] = [ctx.agent]
    result = await orchestrator.run(replace(req, message="现在呼吸困难", intent=IntentCategory.APPOINTMENT_CREATE))
    assert result.escalated and result.artifacts[0].type == "contact_info"
    assert ctx.redis.strings == before_strings and ctx.redis.hashes == before_hashes
    assert (await invoke_tool(ctx.agent, "prepare_appointment", req))["data"]["proposal_id"] == proposal["data"]["proposal_id"]
    assert (await invoke_tool(ctx.agent, "list_appointments", req))["data"] == {"items": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["没有突然失去意识", "现在呼吸困难是什么意思？", "如果有人突然失去意识怎么办？"])
async def test_preclassified_false_emergency_continues_normal_flow(message):
    orchestrator = make_orchestrator()
    result = await orchestrator.run(make_request(message=message, intent=IntentCategory.EMERGENCY, urgency=UrgencyLevel.CRITICAL, entities={}))
    assert result.agent_type is AgentType.GENERAL and not result.escalated
    assert EMERGENCY_RESPONSE not in result.response and result.artifacts == []


@pytest.mark.asyncio
async def test_negative_emergency_clause_does_not_erase_real_appointment_intent():
    orchestrator = make_orchestrator()
    result = await orchestrator.run(make_request(message="没有突然失去意识，请查明天儿科号源", intent=IntentCategory.SLOT_QUERY, urgency=UrgencyLevel.CRITICAL, entities={}))
    assert result.agent_type is AgentType.APPOINTMENT and not result.escalated


@pytest.mark.asyncio
async def test_general_handoff_uses_hospital_contact_without_delivery_claim():
    service = HospitalService()
    client = FakeClient(error=AssertionError("人工导诊无需模型"))
    agent = EscalationAgent(client, "test", hospital_service=service)
    req = make_request(message="我希望联系人工导诊了解报到流程", intent=IntentCategory.HUMAN_HANDOFF)
    result = await agent.handle(req)
    contact = result.artifacts[0].data
    expected = service.data.contact_info.model_dump(mode="json")
    expected["summary"] = req.message
    assert contact == expected
    assert all(expected[key] in result.content for key in ("phone", "hours", "location"))
    assert contact["delivery"] == "contact_only" and not client.calls
    assert "尚未向工作人员提交" in result.content and "已提交" not in result.content and "正在接入真人" not in result.content
    assert EMERGENCY_RESPONSE not in result.content
    assert service.data.contact_info.summary == ""


def test_run_parallel_enters_both_agents_and_preserves_results_when_composer_fails():
    async def scenario():
        entered = {agent_type: asyncio.Event() for agent_type in (
            AgentType.GUIDANCE, AgentType.APPOINTMENT,
        )}
        release = asyncio.Event()
        contents = {
            AgentType.GUIDANCE: "就诊请携带有效证件。",
            AgentType.APPOINTMENT: "请确认希望查询的日期。",
        }

        class CoordinatedClient:
            def __init__(self, agent_type):
                self.agent_type = agent_type
                self.calls = []
                self.messages = self

            async def create(self, **kwargs):
                self.calls.append(kwargs)
                entered[self.agent_type].set()
                await release.wait()
                return SimpleNamespace(content=[SimpleNamespace(
                    type="text", text=contents[self.agent_type],
                )])

        clients = {agent_type: CoordinatedClient(agent_type) for agent_type in entered}
        composer_client = FakeClient(error=RuntimeError("provider down"))
        orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
        orchestrator._pool = {
            AgentType.GUIDANCE: [GuidanceAgent(clients[AgentType.GUIDANCE], "test-model")],
            AgentType.APPOINTMENT: [AppointmentAgent(clients[AgentType.APPOINTMENT], "test-model")],
        }
        orchestrator._composer = ResponseComposer(composer_client, "test-model")
        orchestrator._recent_tool_traces = deque(maxlen=10)
        pending = asyncio.create_task(orchestrator.run(make_request()))
        try:
            # Both agents must enter before either can finish; timeout only prevents a hang.
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in entered.values())), timeout=2,
            )
            assert not pending.done()
            assert composer_client.calls == []
        finally:
            release.set()
            result = await asyncio.wait_for(pending, timeout=2)

        assert all(len(client.calls) == 1 for client in clients.values())
        assert len(composer_client.calls) == 1
        assert result.primary_agent is AgentType.APPOINTMENT
        assert result.supporting_agents == [AgentType.GUIDANCE]
        assert result.agent_types == [AgentType.APPOINTMENT, AgentType.GUIDANCE]
        assert result.response == (
            f"{contents[AgentType.APPOINTMENT]}\n\n补充说明：\n{contents[AgentType.GUIDANCE]}"
        )

    asyncio.run(scenario())


def make_orchestrator(**clients):
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        cls.agent_type: [cls(clients.get(cls.agent_type.value, FakeClient(
            response=SimpleNamespace(content=[SimpleNamespace(type="text", text=f"{cls.agent_type.value} 答复")]),
        )), "test-model")]
        for cls in (GeneralAgent, GuidanceAgent, AppointmentAgent, EscalationAgent)
    }
    orchestrator._composer = ResponseComposer(FakeClient(error=RuntimeError("provider down")), "test-model")
    orchestrator._recent_tool_traces = deque(maxlen=20)
    return orchestrator


@pytest.mark.parametrize("intent,message,expected", [
    (IntentCategory.HOSPITAL_INFO, "医院门诊时间", AgentType.GENERAL),
    (IntentCategory.DEPARTMENT_INFO, "儿科介绍", AgentType.GENERAL),
    (IntentCategory.DOCTOR_INFO, "明天有哪些医生出诊", AgentType.GENERAL),
    (IntentCategory.SLOT_QUERY, "明天儿科还有号吗", AgentType.APPOINTMENT),
    (IntentCategory.APPOINTMENT_CREATE, "选择这个号帮我预约", AgentType.APPOINTMENT),
    (IntentCategory.APPOINTMENT_STATUS, "查看预约记录", AgentType.APPOINTMENT),
    (IntentCategory.APPOINTMENT_CANCEL, "取消这条预约", AgentType.APPOINTMENT),
    (IntentCategory.VISIT_PREPARATION, "第一次就诊要带什么", AgentType.GUIDANCE),
    (IntentCategory.VISIT_PROCESS, "到院后在哪里报到", AgentType.GUIDANCE),
    (IntentCategory.WAYFINDING, "从门诊大厅怎么去药房", AgentType.GUIDANCE),
    (IntentCategory.HUMAN_HANDOFF, "联系人工导诊", AgentType.ESCALATION),
    (IntentCategory.EMERGENCY, "现在呼吸困难", AgentType.ESCALATION),
])
def test_hospital_intents_route_to_their_single_domain(intent, message, expected):
    orchestrator = make_orchestrator()
    req = make_request(message=message, intent=intent, urgency=UrgencyLevel.LOW, entities={})
    decision = orchestrator._route_decision(req)
    assert decision.primary_agent is expected
    assert decision.supporting_agents == []
    assert orchestrator._route(intent, UrgencyLevel.LOW) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("preclassified", [True, False])
@pytest.mark.parametrize("intent,message", [
    (IntentCategory.APPOINTMENT_CREATE, "请为当前就诊人准备预约，号源编号：slot:2026-09-17:schedule_doctor_xu_morning"),
    (IntentCategory.APPOINTMENT_CANCEL, "请准备取消预约，预约编号：appointment-example"),
    (IntentCategory.APPOINTMENT_CREATE, "资料都齐了，请准备预约这个号。"),
    (IntentCategory.APPOINTMENT_CANCEL, "请核对预约资料，再准备取消预约。"),
])
async def test_ui_prepare_actions_execute_only_appointment_without_guidance_or_composition(intent, message, preclassified):
    from test_intent_recognizer import make_recognizer
    appointment = FakeClient(response=SimpleNamespace(content=[SimpleNamespace(type="text", text="请核对待确认资料。")]))
    guidance = FakeClient(error=AssertionError("纯预约准备不应调用指引角色"))
    orchestrator = make_orchestrator(appointment=appointment, guidance=guidance)
    recognizer = make_recognizer(intent.value)
    orchestrator._intent_recognizer = recognizer
    assert recognizer._pattern_recognize(message)["intent"] is intent
    entities = recognizer._extract_entities(message)
    req = make_request(message=message, intent=intent if preclassified else None,
                       urgency=UrgencyLevel.LOW if preclassified else None,
                       entities=entities if preclassified else {}, intent_group="appointment" if preclassified else None)
    result = await orchestrator.run(req)
    assert result.agent_types == [AgentType.APPOINTMENT]
    assert result.supporting_agents == [] and result.response == "请核对待确认资料。"
    assert len(appointment.calls) == 1 and guidance.calls == []
    assert orchestrator._composer._client.calls == []
    scores = orchestrator._domain_scores(req)
    assert scores[AgentType.GUIDANCE] == 0
    assert scores[AgentType.APPOINTMENT] >= .75


@pytest.mark.parametrize("message", [
    "查一下明天儿科的号，顺便告诉我要带什么。",
    "请准备预约这个号，并告诉我需要准备什么。",
    "查明天儿科号源，并说明就诊准备。",
    "请准备取消预约，再说说首次就诊需要准备哪些资料。",
    "准备预约，顺便提供就诊材料和报到流程。",
])
def test_explicit_preparation_requests_still_add_guidance_to_appointment(message):
    orchestrator = make_orchestrator()
    req = make_request(message=message, intent=IntentCategory.APPOINTMENT_CREATE,
                       urgency=UrgencyLevel.LOW, entities={})
    decision = orchestrator._route_decision(req)
    assert decision.primary_agent is AgentType.APPOINTMENT
    assert decision.supporting_agents == [AgentType.GUIDANCE]
    assert orchestrator._domain_scores(req)[AgentType.GUIDANCE] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("preclassified", [True, False])
@pytest.mark.parametrize("message", [
    "给我查明天儿科门诊号，并说明第一次带孩子来需要的资料。",
    "查明天儿科的号，顺便列出儿童就诊所需资料。",
    "帮孩子查明天儿科号源，并说明就诊资料。",
    "查明天儿科号源，再说明携带资料的要求。",
    "查询明天儿科号源，第一次来需要哪些资料？",
    "查明天儿科号源，并告诉我带哪些资料。",
])
async def test_preparation_document_synonyms_execute_both_roles_and_real_cards(hospital_tools_context, message, preclassified):
    from test_intent_recognizer import make_recognizer
    ctx = hospital_tools_context
    appointment = SequenceClient(tool_blocks("search_slots", {"department": "儿科", "date": "明天"}),
                                 text_blocks("已查询实际号源。"))
    guidance = SequenceClient(tool_blocks("get_visit_checklist", {"department": "儿科", "visit_type": "child"}),
                              text_blocks("已查询儿童就诊资料。"))
    orchestrator = make_orchestrator()
    orchestrator._pool[AgentType.APPOINTMENT] = [AppointmentAgent(
        appointment, "test-model", hospital_service=ctx.service, visit_store=ctx.visits)]
    orchestrator._pool[AgentType.GUIDANCE] = [GuidanceAgent(
        guidance, "test-model", hospital_service=ctx.service, visit_store=ctx.visits)]
    recognizer = make_recognizer(IntentCategory.SLOT_QUERY.value)
    orchestrator._intent_recognizer = recognizer
    request = replace(ctx.requests["patient_child"], message=message,
                      intent=IntentCategory.SLOT_QUERY if preclassified else None,
                      urgency=UrgencyLevel.LOW if preclassified else None,
                      entities=recognizer._extract_entities(message) if preclassified else {})

    response = await orchestrator.run(request)

    assert response.primary_agent is AgentType.APPOINTMENT
    assert response.supporting_agents == [AgentType.GUIDANCE]
    assert response.agent_types == [AgentType.APPOINTMENT, AgentType.GUIDANCE]
    assert {artifact.type for artifact in response.artifacts} == {"slot_list", "visit_checklist"}
    assert {(trace["agent_type"], trace["tool_name"]) for trace in response.tool_traces} == {
        ("appointment", "search_slots"), ("guidance", "get_visit_checklist")}
    assert len(appointment.calls) == len(guidance.calls) == 2
    assert all(trace["success"] for trace in response.tool_traces)


def test_low_confidence_ambiguous_request_is_clarified_without_agent_call():
    client = FakeClient(error=AssertionError("clarification must not call a model"))
    orchestrator = make_orchestrator(general=client)
    result = asyncio.run(orchestrator.run(make_request(
        message="帮我处理那个事情", intent=IntentCategory.OTHER, intent_confidence=0.2,
    )))
    assert result.agent_type is AgentType.GENERAL
    assert "请补充" in result.response and "就诊准备" in result.response
    assert client.calls == []


def test_domain_scores_combine_intent_keywords_and_entities():
    orchestrator = make_orchestrator()
    base = orchestrator._domain_scores(make_request(message="", intent=IntentCategory.SLOT_QUERY, entities={}))
    keywords = orchestrator._domain_scores(make_request(message="号源", intent=IntentCategory.SLOT_QUERY, entities={}))
    entities = orchestrator._domain_scores(make_request(message="", intent=IntentCategory.SLOT_QUERY, entities={
        "selection_index": ["1"], "date": ["2026-09-17"],
    }))
    assert base[AgentType.APPOINTMENT] == 0.75
    assert keywords[AgentType.APPOINTMENT] > base[AgentType.APPOINTMENT]
    assert entities[AgentType.APPOINTMENT] > base[AgentType.APPOINTMENT]
    route = orchestrator._domain_scores(make_request(message="", intent=IntentCategory.OTHER, entities={
        "origin": ["门诊大厅"], "destination": ["药房"],
    }))
    assert route[AgentType.GUIDANCE] == 0.2


def test_same_type_pool_still_uses_performance_score_and_monitor_penalty():
    orchestrator = make_orchestrator()
    reliable = AppointmentAgent(FakeClient(), "reliable")
    unreliable = AppointmentAgent(FakeClient(), "unreliable")
    reliable.stats.total, reliable.stats.success, reliable.stats.total_ms = 10, 10, 1000
    unreliable.stats.total, unreliable.stats.success, unreliable.stats.total_ms = 10, 3, 1000
    orchestrator._pool[AgentType.APPOINTMENT] = [unreliable, reliable]
    assert orchestrator._best_agent(AgentType.APPOINTMENT) is reliable
    reliable.stats.monitor_penalty = 1.0
    assert orchestrator._best_agent(AgentType.APPOINTMENT) is unreliable


def test_preclassified_and_direct_requests_pass_equal_entities_and_preserve_identity():
    class Recognizer:
        def __init__(self):
            self.calls = []
            self.result = SimpleNamespace(
                intent=IntentCategory.SLOT_QUERY, intent_group="appointment",
                urgency=UrgencyLevel.LOW, confidence=0.95,
                entities={"department": ["儿科"], "date": ["2026-09-17"], "period": ["afternoon"]},
            )

        async def recognize(self, message, history=None):
            self.calls.append((message, history))
            return self.result

    client = FakeClient(response=SimpleNamespace(content=[SimpleNamespace(type="text", text="请核对查询条件。")]))
    orchestrator = make_orchestrator(appointment=client)
    recognizer = Recognizer()
    orchestrator._intent_recognizer = recognizer
    history = [{"role": "user", "content": "我想查儿科的号"}]
    shared = dict(message="明天下午的号，我自称是另一个患者", history=history, patient_id="patient_child")
    preclassified = make_request(**shared, urgency=UrgencyLevel.LOW, intent_confidence=0.95)
    direct = make_request(**shared, intent=None, intent_group=None, urgency=None, entities={"doctor": ["过时信息"]})

    asyncio.run(orchestrator.run(preclassified))
    asyncio.run(orchestrator.run(direct))

    assert recognizer.calls == [(shared["message"], history)]
    assert direct.entities == preclassified.entities == recognizer.result.entities
    assert (direct.user_id, direct.patient_id, direct.conv_id) == ("u1", "patient_child", "c1")
    packets = [json.loads(next(message["content"].split("\n", 1)[1] for message in call["messages"]
                              if isinstance(message["content"], str) and message["content"].startswith("[角色输入契约]")))
               for call in client.calls]
    assert packets[0] == packets[1]
    direct.entities["date"].append("2026-09-18")
    assert recognizer.result.entities["date"] == ["2026-09-17"]


def test_failed_supporting_agent_preserves_primary_and_does_not_claim_support_success():
    orchestrator = make_orchestrator(
        appointment=FakeClient(response=SimpleNamespace(content=[SimpleNamespace(type="text", text="请核对预约查询条件。") ])),
        guidance=FakeClient(error=RuntimeError("guidance unavailable")),
        general=FakeClient(error=RuntimeError("fallback unavailable")),
    )
    result = asyncio.run(orchestrator.run(make_request()))
    assert result.response == "请核对预约查询条件。"
    assert result.agent_types == [AgentType.APPOINTMENT]
    assert result.primary_agent is AgentType.APPOINTMENT
    assert result.supporting_agents == [AgentType.GUIDANCE]
    assert "材料已" not in result.response
    assert orchestrator._composer._client.calls == []


def test_unavailable_domain_falls_back_to_general_pool():
    orchestrator = make_orchestrator()
    orchestrator._pool[AgentType.APPOINTMENT] = []
    result = asyncio.run(orchestrator._execute(make_request(), AgentType.APPOINTMENT))
    assert result.success is True and result.agent_type is AgentType.GENERAL


def test_pool_and_model_overrides_use_the_four_hospital_role_names(monkeypatch):
    monkeypatch.setenv("MEDIPET_GUIDANCE_MODEL", "guidance-test-model")
    monkeypatch.setenv("MEDIPET_APPOINTMENT_MODEL", "appointment-test-model")
    with patch("agents.agent_orchestrator.AsyncAnthropic", return_value=FakeClient()), \
            patch("agents.agent_orchestrator.IntentRecognizer"):
        orchestrator = AgentOrchestrator(api_key="fake-key", model="default-test-model")
    assert set(orchestrator._pool) == {
        AgentType.GENERAL, AgentType.GUIDANCE, AgentType.APPOINTMENT, AgentType.ESCALATION,
    }
    assert orchestrator._pool[AgentType.GUIDANCE][0]._model == "guidance-test-model"
    assert orchestrator._pool[AgentType.APPOINTMENT][0]._model == "appointment-test-model"
    assert Request("你好", "u1", "c1").patient_id is None
