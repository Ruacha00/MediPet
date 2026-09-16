"""U001 verifies classification, routing and real information tools with a fake model."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agents.agent_orchestrator import (
    AgentOrchestrator, AgentType, GeneralAgent, MedicationAgent, Request, TriageAgent,
)
from agents.tools import build_health_tools
from core.intent_recognizer import IntentCategory as Intent, UrgencyLevel
from hospital.models import Artifact, VisitMessage
from hospital.service import HospitalService
from health.reports import preprocess_report
from memory.visit_store import VisitStore
from test_visit_memory import MemoryRedis
from test_agent_orchestrator import FakeClient, make_request
from test_intent_recognizer import make_recognizer


@pytest.mark.parametrize("message,intent", [
    ("咳嗽流鼻涕该挂哪个科", Intent.SYMPTOM_QUERY),
    ("眼睛发红发痒", Intent.SYMPTOM_QUERY),
    ("儿童发烧该看哪个科", Intent.SYMPTOM_QUERY),
    ("布洛芬200mg普通片有哪些禁忌", Intent.MEDICATION_QUERY),
    ("对乙酰氨基酚与华法林相互作用", Intent.MEDICATION_QUERY),
    ("化验单参考范围是什么意思", Intent.REPORT_QUERY),
    ("血红蛋白 120 g/L 参考范围 115-150", Intent.REPORT_QUERY),
    ("可以上传报告图片吗", Intent.REPORT_QUERY),
    ("从大厅怎么去药房", Intent.WAYFINDING),
    ("取药办理流程", Intent.VISIT_PROCESS),
    ("儿科的介绍是什么", Intent.DEPARTMENT_INFO),
    ("查询明天儿科号源", Intent.SLOT_QUERY),
])
def test_new_intents_keep_original_business_distinctions(message, intent):
    recognizer = make_recognizer(intent.value)
    assert recognizer._pattern_recognize(message)["intent"] is intent
    result = asyncio.run(recognizer.recognize(message))
    assert result.intent is intent
    assert result.entities is not None


def orchestrator():
    with patch("agents.agent_orchestrator.AsyncAnthropic", return_value=FakeClient()), patch("core.intent_recognizer.AsyncAnthropic", return_value=FakeClient()):
        return AgentOrchestrator("test-only", model="fake-model")


@pytest.mark.parametrize("intent,message,expected", [
    (Intent.SYMPTOM_QUERY, "咳嗽流鼻涕该挂哪个科", AgentType.TRIAGE),
    (Intent.REPORT_QUERY, "整理检查报告参考范围", AgentType.TRIAGE),
    (Intent.MEDICATION_QUERY, "布洛芬的说明书", AgentType.MEDICATION),
    (Intent.WAYFINDING, "药房怎么去", AgentType.GUIDANCE),
    (Intent.SLOT_QUERY, "明天儿科号源", AgentType.APPOINTMENT),
])
def test_health_and_existing_intents_route_to_available_roles(intent, message, expected):
    decision = orchestrator()._route_decision(make_request(intent=intent, message=message, entities={}, urgency=UrgencyLevel.LOW))
    assert decision.primary_agent is expected


def test_six_roles_are_instantiated_and_health_tools_are_isolated():
    app = orchestrator()
    assert set(app._pool) == set(AgentType)
    triage = TriageAgent(FakeClient(), "fake")
    medication = MedicationAgent(FakeClient(), "fake")
    general = GeneralAgent(FakeClient(), "fake")
    assert set(triage.get_tools()) == {"triage_symptoms", "preprocess_report", "read_current_report"}
    assert set(medication.get_tools()) == {"medication_information"}
    assert not set(triage.get_tools()) & set(general.get_tools())
    assert "prepare_appointment" not in triage.get_tools()


def test_explicit_medication_and_symptom_query_has_both_roles():
    decision = orchestrator()._route_decision(make_request(
        intent=Intent.MEDICATION_QUERY, message="布洛芬有哪些禁忌，咳嗽流鼻涕该挂哪个科？",
        entities={}, urgency=UrgencyLevel.LOW,
    ))
    assert decision.primary_agent is AgentType.MEDICATION
    assert AgentType.TRIAGE in decision.supporting_agents


@pytest.mark.parametrize("args", [
    {"drug_name": "布洛芬", "other_drugs": "华法林"},
    {"drug_name": "布洛芬", "other_drugs": [False]},
    {"drug_name": "未提到的药物"},
    {"drug_name": "布洛芬", "other_drugs": ["未提到的药物"]},
])
def test_medication_tool_rejects_wrong_types_or_invented_medicines(args):
    handler = build_health_tools("medication")["medication_information"].handler
    result = handler(make_request(message="布洛芬的说明书", history=[]), args)
    assert not result["success"] and result["error_code"] == "invalid_input"


@pytest.mark.parametrize("message,name", [
    ("布洛芬缓释胶囊怎么吃", "布洛芬"),
    ("布洛芬（缓释胶囊）的说明书", "布洛芬"),
    ("布洛芬400mg片的禁忌", "布洛芬"),
    ("布洛芬片规格是400毫克", "布洛芬片"),
    ("布洛芬，规格400mg", "布洛芬"),
    ("布洛芬是缓释胶囊", "布洛芬"),
    ("布洛芬200mg缓释胶囊", "布洛芬200mg"),
    ("复方对乙酰氨基酚的用法", "对乙酰氨基酚"),
    ("儿童布洛芬混悬液", "布洛芬"),
    ("ibuprofen extended release", "ibuprofen"),
])
def test_medication_wrapper_rejects_model_omitting_formulation_or_strength(message, name):
    handler = build_health_tools("medication")["medication_information"].handler
    result = handler(make_request(message=message, history=[]), {"drug_name": name})
    assert not result["success"] and result["error_code"] == "invalid_input"
    assert not result.get("artifacts")
    assert "剂型或规格" in result["error"]


def test_medication_wrapper_keeps_formulation_from_recent_user_history_and_other_drugs():
    handler = build_health_tools("medication")["medication_information"].handler
    previous = [{"role": "user", "content": "我问的是布洛芬缓释胶囊"}]
    result = handler(make_request(message="它有哪些禁忌？", history=previous), {"drug_name": "布洛芬"})
    assert not result["success"] and result["error_code"] == "invalid_input"
    combination = handler(make_request(message="布洛芬与复方对乙酰氨基酚能否同服", history=[]),
                          {"drug_name": "布洛芬", "other_drugs": ["对乙酰氨基酚"]})
    assert not combination["success"] and combination["error_code"] == "invalid_input"


@pytest.mark.parametrize("name,known", [("布洛芬200mg普通片", True), ("布洛芬缓释胶囊", False), ("布洛芬400mg片", False)])
def test_medication_wrapper_preserves_complete_formulation_for_catalog_lookup(name, known):
    handler = build_health_tools("medication")["medication_information"].handler
    result = handler(make_request(message=f"请查{name}的说明书", history=[]), {"drug_name": name})
    assert result["success"]
    assert bool(result["data"]["sources"]) is known
    if not known:
        assert result["data"]["title"] == "药品资料未收录"


def test_medication_wrapper_does_not_confuse_taken_quantity_or_previous_message_with_formulation():
    handler = build_health_tools("medication")["medication_information"].handler
    result = handler(make_request(message="我昨天吃了2片布洛芬200mg普通片，请查标签", history=[]),
                     {"drug_name": "布洛芬200mg普通片"})
    assert result["success"] and result["data"]["sources"]
    separate = handler(make_request(message="布洛芬的说明书", history=[{"role": "user", "content": "另一个药是缓释"}]),
                       {"drug_name": "布洛芬"})
    assert separate["success"] and separate["data"]["sources"]


@pytest.mark.parametrize("role,tool,message,args,kind", [
    ("triage", "triage_symptoms", "我咳嗽流鼻涕，该挂哪个科", {}, "triage_guidance"),
    ("triage", "preprocess_report", "血红蛋白 120 g/L 参考范围 115-150", {}, "report_summary"),
    ("medication", "medication_information", "布洛芬200mg普通片的说明书", {"drug_name": "布洛芬200mg普通片"}, "medication_info"),
])
def test_health_tools_create_valid_artifacts_from_real_services(role, tool, message, args, kind):
    result = build_health_tools(role)[tool].handler(make_request(message=message), args)
    assert result["success"]
    assert Artifact.model_validate(result["artifacts"][0]).type == kind
    assert result["artifacts"][0]["data"] == result["data"]


@pytest.mark.parametrize("cls,tool,message,args,kind", [
    (TriageAgent, "triage_symptoms", "眼睛发红发痒该看哪个科", {}, "triage_guidance"),
    (MedicationAgent, "medication_information", "布洛芬的说明书", {"drug_name": "布洛芬"}, "medication_info"),
])
def test_model_tool_roundtrip_preserves_health_cards_and_trace(cls, tool, message, args, kind):
    class Model:
        def __init__(self):
            self.calls = []
            self.messages = self

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(
                    type="tool_use", id="health-tool-1", name=tool, input=args,
                )])
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="请核对资料卡，此信息不替代医生诊断。")])

    model = Model()
    result = asyncio.run(cls(model, "fake").handle(make_request(message=message)))
    assert result.success and len(model.calls) == 2
    assert result.artifacts[0].type == kind
    assert result.tools_used == [tool]
    assert result.tool_traces[0]["success"]
    assert kind in model.calls[1]["messages"][-1]["content"][0]["content"]


@pytest.mark.asyncio
async def test_uploaded_report_followup_reads_only_current_patient_and_visit():
    visits = VisitStore(MemoryRedis(), prefix="medipet:test:report-followup:")
    await visits.initialize_patients(HospitalService().data.patients)
    own = await visits.create_visit("anonymous", "patient_self")
    child = await visits.create_visit("anonymous", "patient_child")
    other = await visits.create_visit("anonymous", "patient_self")
    raw = "WBC 12.5 mg/L 4-10"
    artifact = Artifact(id="report_summary:test", type="report_summary", data=preprocess_report(raw).model_dump(mode="json"))
    await visits.append_messages("anonymous", own.conv_id, [VisitMessage(
        user_id="anonymous", patient_id="patient_self", conv_id=own.conv_id,
        message_id="report:test", role="assistant", content="报告整理", artifacts=[artifact], created_at=visits.now(),
    )])
    handler = build_health_tools("triage", visits)["read_current_report"].handler
    req = make_request(user_id="anonymous", patient_id="patient_self", conv_id=own.conv_id, message="这份报告哪些超出了参考区间？")
    result = await handler(req, {})
    assert result["success"] and result["data"]["extracted_text"] == raw
    assert result["data"]["observations"][0]["flag"] == "above"
    for visit in (child, other):
        missing = await handler(make_request(user_id="anonymous", patient_id=visit.patient_id, conv_id=visit.conv_id), {})
        assert missing["error_code"] == "not_found"
    wrong = await handler(make_request(user_id="anonymous", patient_id="patient_child", conv_id=own.conv_id), {})
    assert wrong["error_code"] == "identity_conflict"
    await visits.update_visit("anonymous", own.conv_id, archived=True)
    assert (await handler(req, {}))["error_code"] == "visit_archived"
