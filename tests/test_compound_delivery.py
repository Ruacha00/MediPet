import json
from dataclasses import replace

import pytest

from agents.agent_orchestrator import AgentType, GuidanceAgent
from agents.task_requirements import compound_visit_request, explicit_visit_type
from core.intent_recognizer import IntentCategory, UrgencyLevel
from hospital.service import HospitalService
from test_agent_orchestrator import (
    SequenceClient, make_orchestrator, make_request, text_blocks, tool_blocks, invoke_tool,
    hospital_tools_context,
)


@pytest.mark.parametrize("message", [
    "我需要明天儿科的号源列表，也要了解儿童首次就诊需带的证件。",
    "能一起查明天内科可选时段和成人首次就诊材料吗？",
    "儿童明天第一次去儿科，请查可选门诊号，并列明要带的东西。",
])
def test_explicit_compound_survives_low_confidence_other(message):
    orchestrator = make_orchestrator()
    req = make_request(message=message, intent=IntentCategory.OTHER,
                       intent_confidence=0.2, urgency=UrgencyLevel.LOW)
    assert not orchestrator._needs_clarification(req)
    decision = orchestrator._route_decision(req)
    assert set(decision.agent_types) == {AgentType.APPOINTMENT, AgentType.GUIDANCE}


@pytest.mark.asyncio
async def test_checklist_uses_explicit_child_requirement_over_generic_model_args():
    agent = GuidanceAgent(SequenceClient(), "test", hospital_service=HospitalService())
    req = make_request(message="请列出儿童首次就诊需带的证件。")
    result = await invoke_tool(agent, "get_visit_checklist", req, department="儿科", visit_type="first")
    assert result["success"]
    assert result["artifacts"][0]["data"]["visit_type"] == "child"
    assert result["effective_input"] == {"department": "儿科", "visit_type": "child"}


@pytest.mark.asyncio
async def test_compound_guidance_first_call_requires_real_checklist():
    client = SequenceClient(tool_blocks("get_visit_checklist", {"visit_type": "child"}), text_blocks())
    agent = GuidanceAgent(client, "test", hospital_service=HospitalService())
    result = await agent.handle(make_request())
    assert result.success
    assert client.calls[0].get("tool_choice") == {"type": "tool", "name": "get_visit_checklist"}
    assert "tool_choice" not in client.calls[1]
    assert result.artifacts[0].type == "visit_checklist"


@pytest.mark.parametrize("message", [
    "不要查号源，只列首次就诊材料。",
    "查明天号源，不用提供材料。",
    "帮我准备预约这个号，资料已经齐了。",
    "请准备取消预约，核对预约资料。",
    "明天医院什么时候开门？",
])
def test_declined_or_unrelated_tasks_do_not_force_compound_tools(message):
    assert not compound_visit_request(message)


def test_family_identity_does_not_invent_child_age():
    assert explicit_visit_type("家属首次就诊材料") == "first"
    assert explicit_visit_type("儿童与成人的材料分别是什么") is None


def test_explicit_decline_overrides_earlier_topic_mention():
    assert not compound_visit_request("我只想了解号源查询规则和首次材料，不用实际查询号源。")


@pytest.mark.asyncio
async def test_previous_child_subject_does_not_override_current_self_materials():
    req = make_request(message="孩子已经就诊过了，现在给我本人查询明天内科号源和首次就诊材料。",
                       patient_id="patient_self")
    agent = GuidanceAgent(SequenceClient(), "test", hospital_service=HospitalService())
    result = await invoke_tool(agent, "get_visit_checklist", req, department="内科", visit_type="first")
    assert result["success"]
    assert result["artifacts"][0]["data"]["visit_type"] == "first"


@pytest.mark.asyncio
async def test_declined_slot_query_cannot_replace_existing_selection(hospital_tools_context):
    ctx = hospital_tools_context
    req = ctx.requests["patient_self"]
    await invoke_tool(ctx.agent, "search_slots", req, department="内科", date="明天")
    before = await ctx.visits.get_selection(req.user_id, req.conv_id)
    declined = replace(req, message="我只想了解号源查询规则和首次材料，不用实际查询号源。")
    result = await invoke_tool(ctx.agent, "search_slots", declined, department="眼科", date="后天")
    assert not result["success"] and result["artifacts"] == []
    assert await ctx.visits.get_selection(req.user_id, req.conv_id) == before


def test_declining_cancellation_does_not_block_read_only_query():
    assert compound_visit_request("不用取消预约，只查号源和首次材料。")


@pytest.mark.asyncio
async def test_compound_tool_failure_remains_visible_without_fabricated_card():
    client = SequenceClient(tool_blocks("get_visit_checklist", {"department": "未知科室"}),
                            text_blocks("未收录该科室材料，请核对科室。"))
    agent = GuidanceAgent(client, "test", hospital_service=HospitalService())
    response = await agent.handle(make_request())
    assert response.artifacts == []
    assert not response.tool_traces[0]["success"]
    assert response.tool_traces[0]["error_code"] == "not_found"
