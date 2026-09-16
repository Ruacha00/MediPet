"""评测器的假模型/隔离内存存储检查；通过率不是实际模型能力证据。"""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agents.agent_orchestrator import AgentType, OrchestratorResult
from agents.tools import build_hospital_tools
from core.emergency import detect_emergency
from core.intent_recognizer import IntentCategory
from evaluation.evaluator import (
    DEFAULT_BOUNDARY_CASES, DEFAULT_DIALOG_CASES, DEFAULT_INTENT_CASES,
    EndToEndEvaluator, EvaluationRuntime, IntentEvaluator, IntentTestCase,
    LLMJudge, QualityScores,
)
from hospital.models import Artifact, VisitIdentity
from hospital.service import HospitalService
from hospital.store import HospitalStore
from memory.conversation_memory import MemoryManager
from memory.visit_store import VisitStore
from monitor.performance_monitor import PerformanceMonitor
from test_appointment_flow import BusinessRedis
from test_conversation_memory import Chroma, Model


class Recognizer:
    def __init__(self, predictions=None):
        self.predictions = predictions or {case.message: case.expected_intent for case in DEFAULT_INTENT_CASES}
        self.calls = []

    async def recognize(self, message, history=None):
        self.calls.append((message, history))
        predicted = self.predictions[message]
        if isinstance(predicted, Exception):
            raise predicted
        return SimpleNamespace(intent=SimpleNamespace(value=predicted), confidence=1, reasoning="假分类器")


class Judge:
    def __init__(self, values=None):
        self.values = list(values or [])
        self.calls = []

    async def judge(self, question, response, context=None):
        self.calls.append((question, response, context))
        value = self.values.pop(0) if self.values else 0.9
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, QualityScores) else QualityScores(value, value, value, value)


class Chat:
    def __init__(self):
        self.calls = []

    async def run(self, req):
        self.calls.append(req)
        if req.message == "调用失败":
            raise RuntimeError("fake model unavailable")
        return OrchestratorResult(req.request_id, "请按材料清单准备。", AgentType.GUIDANCE,
                                  IntentCategory.VISIT_PREPARATION, latency_ms=12,
                                  agent_types=[AgentType.GUIDANCE], primary_agent=AgentType.GUIDANCE,
                                  tools_used=["get_visit_checklist"],
                                  tool_traces=[{"tool_name": "get_visit_checklist", "success": True, "latency_ms": 2}])


class ScenarioChat:
    """固定工具计划替代模型决策，业务读取/准备仍调用真实工具与医院服务。"""
    def __init__(self, case, service, visits):
        self.case, self.service, self.visits = case, service, visits
        self.calls = []
        self.tools = {**build_hospital_tools("appointment", service, visits),
                      **build_hospital_tools("guidance", service, visits)}
        self.model_calls = 0

    async def run(self, req):
        turn = len(self.calls)
        self.calls.append(req)
        if detect_emergency(req.message):
            return OrchestratorResult(req.request_id, "请立即联系现场医护人员。", AgentType.ESCALATION,
                                      IntentCategory.EMERGENCY, agent_types=[AgentType.ESCALATION])
        self.model_calls += 1
        case_id = self.case.get("id")
        results, traces, names = [], [], []

        async def call(name, **kwargs):
            result = await self.tools[name].handler(req, kwargs)
            results.extend(Artifact.model_validate(value) for value in result.get("artifacts", []))
            traces.append({"kind": "tool_call", "tool_name": name, "success": result["success"],
                           "error_code": result.get("error_code"), "latency_ms": 1})
            names.append(name)
            return result

        async def search(**kwargs):
            return await call("search_slots", department="儿科", **kwargs)

        if case_id == "date-followup":
            await search(date="今天" if turn == 0 else "明天")
        elif case_id == "period-followup":
            await search(date="明天", period="morning" if turn == 0 else "afternoon")
        elif case_id in {"list-selection", "create-confirm", "history-restore", "confirm-followup"}:
            if turn == 0:
                await search(date="明天")
            elif turn == 1:
                await call("prepare_appointment", selection_index=1)
        elif case_id == "cancel-confirm":
            if turn == 0:
                await call("list_appointments", status="active")
            else:
                records = await self.service.get_appointments(VisitIdentity(user_id=req.user_id, patient_id=req.patient_id, conv_id=req.conv_id))
                await call("prepare_cancellation", appointment_id=records.data["items"][0]["appointment_id"])
        elif case_id in {"preparation-followup", "archive-restore"}:
            await call("get_visit_checklist", department="儿科", visit_type="child")
            if case_id == "preparation-followup":
                traces.append({"tool_name": "search_knowledge_base", "success": True,
                               "result_summary": {"result_count": 2},
                               "sources": [{"source_id": "fake-test-materials", "chunk_id": "fake-materials:0"},
                                           {"source_id": "fake-test-process", "chunk_id": "fake-process:0"}]})
        elif case_id == "collaboration":
            if turn == 0:
                await search(date="明天")
                await call("get_visit_checklist", department="儿科", visit_type="child")
            else:
                await call("prepare_appointment", selection_index=1)
        elif case_id == "accessible-route":
            await call("get_wayfinding", origin="hall", destination="ophthalmology-room", mode="normal" if turn == 0 else "accessible")
        elif case_id in {"patient-switch", "reselection", "no-confirmation"}:
            if turn == 0:
                await search(date="明天")
                await call("prepare_appointment", selection_index=1)
            elif case_id == "reselection":
                await call("prepare_appointment", selection_index=2)
            elif case_id == "patient-switch":
                await call("list_appointments")
            # no-confirmation 的“好的”绝不能确认。
        elif case_id == "no-slots":
            await search(date="2026-10-01")
        elif case_id == "retrieval-failure":
            traces.append({"tool_name": "search_knowledge_base", "success": False,
                           "error_code": "retrieval_failed", "result_summary": {"result_count": 0}})
        agents = [AgentType.APPOINTMENT, AgentType.GUIDANCE] if case_id == "collaboration" else [AgentType.GUIDANCE]
        return OrchestratorResult(req.request_id, "来自实际工具的就诊资料。", agents[0], IntentCategory.QUERY,
                                  agent_types=agents, primary_agent=agents[0], tools_used=names,
                                  tool_traces=traces, artifacts=results, latency_ms=5)


class RuntimeFactory:
    def __init__(self, *, faults=True, crash=False, isolated=True):
        self.records, self.closed = [], []
        self.faults, self.crash, self.isolated = faults, crash, isolated

    @asynccontextmanager
    async def __call__(self, case, run_id):
        redis = BusinessRedis()
        prefix = f"medipet:test:eval:{uuid4().hex}:"
        clock = SimpleNamespace(value=datetime.fromisoformat(case.get("clock", "2026-09-16T10:00:00+08:00")))
        visits = VisitStore(redis, prefix=prefix, clock=lambda: clock.value)
        service = HospitalService(store=HospitalStore(redis, prefix), visit_store=visits, clock=lambda: clock.value)
        await visits.initialize_patients(service.data.patients)
        await service.initialize_slots()
        memory = MemoryManager(api_key="fake", redis_client=redis, chroma_client=Chroma(), visit_store=visits)
        memory._client = Model()
        chat = Chat() if self.crash else ScenarioChat(case, service, visits)
        def advance(seconds):
            clock.value += timedelta(seconds=seconds)
        runtime = EvaluationRuntime(chat, memory, visits, service, self.isolated,
                                    ("knowledge_backend_unavailable", "model_unavailable") if self.faults else (),
                                    advance, lambda: getattr(chat, "model_calls", 0))
        self.records.append(runtime)
        try:
            yield runtime
        finally:
            self.closed.append(runtime)


def evaluator(*, judge=None, factory=None, **kwargs):
    return EndToEndEvaluator(Chat(), Recognizer(), "unused", judge=judge or Judge(), case_runtime_factory=factory, **kwargs)


def test_defaults_load_all_dataset_cases_with_identity_and_clock():
    assert (len(DEFAULT_INTENT_CASES), len(DEFAULT_DIALOG_CASES), len(DEFAULT_BOUNDARY_CASES)) == (54, 12, 12)
    assert all(case.patient_id and case.context["clock"] for case in DEFAULT_INTENT_CASES)
    assert all(case["user_id"] == "anonymous" and case["patient_id"] for case in DEFAULT_DIALOG_CASES + DEFAULT_BOUNDARY_CASES)
    assert all(case.expected_intent in {intent.value for intent in IntentCategory} for case in DEFAULT_INTENT_CASES)


@pytest.mark.asyncio
async def test_original_accuracy_macro_f1_and_context_are_preserved():
    recognizer = Recognizer({"a": "a", "b": "a", "c": "b"})
    metrics = await IntentEvaluator(recognizer).evaluate([
        IntentTestCase("a", "a", {"history": [{"role": "user", "content": "上一句"}]}),
        IntentTestCase("b", "b"), IntentTestCase("c", "b"),
    ])
    assert metrics["accuracy"] == pytest.approx(2 / 3, abs=1e-4)
    assert metrics["macro_f1"] == pytest.approx(2 / 3, abs=1e-4)
    assert recognizer.calls[0][1] == [{"role": "user", "content": "上一句"}]


@pytest.mark.asyncio
async def test_quality_call_and_judge_failures_have_no_fake_scores():
    judge = Judge([0.9, RuntimeError("Judge offline"), QualityScores(.5, .5, .5, .5, judge_failed=True, error="parse")])
    report = await evaluator(judge=judge).run(dialog_cases=[
        {"id": "good", "question": "准备"}, {"id": "call", "question": "调用失败"},
        {"id": "judge", "question": "流程"}, {"id": "parse", "question": "材料"},
    ])
    assert report.avg_scores == dict.fromkeys(EndToEndEvaluator.QUALITY_DIMENSIONS, .9)
    assert report.judge_failures == ["judge:turn:0", "parse:turn:0"]
    assert report.call_failures == ["call:execution"]
    assert report.passed == 1 and report.total == 4
    assert all(not result.scores for result in report.results[1:])
    assert report.results[0].metadata["tools_used"] == ["get_visit_checklist"]
    assert report.results[0].metadata["latency_ms"] == 12
    assert report.metadata["valid_quality_count"] == 1


@pytest.mark.asyncio
async def test_intent_call_failure_is_counted_and_other_cases_continue():
    engine = evaluator()
    engine._intent_evaluator = IntentEvaluator(Recognizer({"bad": RuntimeError("offline"), "good": "greeting"}))
    report = await engine.run(intent_cases=[IntentTestCase("bad", "greeting", id="bad"), IntentTestCase("good", "greeting")])
    assert report.avg_scores["intent_accuracy"] == .5
    assert report.call_failures == ["intent:bad"]
    assert report.results[0].metadata["cases"][0]["predicted"] is None
    assert report.results[0].scores["macro_f1"] == pytest.approx(2 / 3, abs=1e-4)


@pytest.mark.asyncio
async def test_agent_fallback_failure_trace_cannot_gain_a_quality_score():
    class FailedChat(Chat):
        async def run(self, req):
            result = await super().run(req)
            result.tool_traces.append({"kind": "agent_execution", "success": False, "error": "model offline"})
            return result
    judge = Judge([1])
    engine = evaluator(judge=judge)
    engine._orchestrator = FailedChat()
    report = await engine.run(dialog_cases=[{"question": "普通请求"}])
    assert not judge.calls and not report.avg_scores and report.call_failures
    assert report.passed == 0 and report.results[0].metadata["execution_failures"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    {"kind": "agent_execution", "success": False, "error": "fake model unavailable"},
    {"kind": "composition", "success": False, "error": "fake composition unavailable"},
    {"kind": "tool_call", "success": False, "call_success": False, "error": "fake tool exception"},
])
async def test_boundary_execution_failure_is_recorded_without_judge(failure):
    factory, judge = RuntimeFactory(), Judge([1])

    @asynccontextmanager
    async def failed_runtime(case, run_id):
        async with factory(case, run_id) as runtime:
            original = runtime.orchestrator.run
            async def run(req):
                response = await original(req)
                response.tool_traces.append(failure)
                return response
            runtime.orchestrator.run = run
            yield runtime

    case = next(case for case in DEFAULT_BOUNDARY_CASES if case["id"] == "negated-emergency")
    report = await evaluator(factory=failed_runtime, judge=judge).run(boundary_cases=[case])
    assert judge.calls == [] and report.avg_scores == {}
    assert report.call_failures == ["negated-emergency:turn:0"]
    execution = next(result for result in report.results if result.metadata.get("kind") == "execution")
    assert not execution.passed and execution.scores == {}
    assert execution.metadata["execution_failures"] == [failure]
    # 已核对的状态断言仍独立记录，不能遮住本轮执行失败。
    assert report.results[-1].test_id == "negated-emergency:business" and report.results[-1].passed
    assert report.passed == 1 and report.total == 2 and report.pass_rate == .5


@pytest.mark.asyncio
@pytest.mark.parametrize("sources,success,per_turn_count", [
    (None, True, 0),
    ([], True, 0),
    ([{"title": "只有标题"}, {"source_id": " "}, {"source": None}, {"doc_id": False}, "not metadata"], True, 0),
    ([{"source": "knowledge/fake-materials.md"}], True, 1),
    ([{"source_id": "fake-materials"}, {"doc_id": "fake-materials"}, {"chunk_id": "fake-materials:0"}], True, 3),
    ([{"source_id": "fake-materials"}], False, 0),
])
async def test_knowledge_source_assertions_require_returned_identifiers(sources, success, per_turn_count):
    factory = RuntimeFactory()

    @asynccontextmanager
    async def source_runtime(case, run_id):
        async with factory(case, run_id) as runtime:
            original = runtime.orchestrator.run
            async def run(req):
                response = await original(req)
                for trace in response.tool_traces:
                    if trace.get("tool_name") == "search_knowledge_base":
                        trace["success"] = success
                        if sources is None:
                            trace.pop("sources", None)
                        else:
                            trace["sources"] = sources
                return response
            runtime.orchestrator.run = run
            yield runtime

    case = next(case for case in DEFAULT_DIALOG_CASES if case["id"] == "preparation-followup")
    report = await evaluator(factory=source_runtime).run(dialog_cases=[case])
    business = report.results[-1]
    # 两轮都声明 result_count=2，该摘要不能替代来源证据。
    traces = [trace for turn in business.metadata["turns"] for trace in turn["tool_traces"]
              if trace.get("tool_name") == "search_knowledge_base"]
    assert len(traces) == 2 and all(trace["result_summary"]["result_count"] == 2 for trace in traces)
    assert business.metadata["facts"]["knowledge.sources.count"] == per_turn_count * 2
    assert business.metadata["facts"]["knowledge_sources.nonempty"] is bool(per_turn_count)
    assert business.passed is bool(per_turn_count)
    if not per_turn_count:
        assert report.metadata["business_failures"] == ["preparation-followup:business"]


@pytest.mark.asyncio
async def test_judge_receives_current_facts_without_history_or_expected_answers():
    case = deepcopy(next(case for case in DEFAULT_DIALOG_CASES if case["id"] == "preparation-followup"))
    case["after_turns"] = [{"action": "archive_visit"}, {"action": "restore_visit"}]
    case["expected_scores"] = {"accuracy": "EXPECTED_SCORE_NOT_FOR_JUDGE"}
    factory, judge = RuntimeFactory(), Judge()
    report = await evaluator(factory=factory, judge=judge).run(dialog_cases=[case])
    assert not report.call_failures
    runtime = factory.records[0]
    request = runtime.orchestrator.calls[0]
    first = report.results[0]
    sent = judge.calls[0][2]
    background = json.loads(sent)
    assert request.history is None
    assert background["prior_context"] == (request.context or None)
    current = background["current_turn"]
    assert current["identity"] == {"user_id": request.user_id, "patient_id": request.patient_id, "conv_id": request.conv_id}
    assert current["business_clock"] == "2026-09-16T10:00:00+08:00"
    assert current["artifacts"] == first.metadata["artifacts"] and current["artifacts"]
    assert current["tool_traces"] == first.metadata["tool_traces"]
    knowledge = next(trace for trace in current["tool_traces"] if trace["tool_name"] == "search_knowledge_base")
    assert knowledge["success"] and knowledge["sources"][0]["source_id"] == "fake-test-materials"
    assert first.metadata["judge_context"] == sent
    assert report.results[-1].metadata["turns"][0]["judge_context"] == sent
    assert all(expression not in sent for expression in case["assertions"])
    assert all(hidden not in sent for hidden in ("archive_visit", "restore_visit", "EXPECTED_SCORE_NOT_FOR_JUDGE"))
    second_background = json.loads(judge.calls[1][2])
    assert second_background["prior_context"] == runtime.orchestrator.calls[1].context
    assert request.message in second_background["prior_context"]


@pytest.mark.asyncio
async def test_judge_background_uses_only_current_patient_and_visit():
    cases = [case for case in DEFAULT_DIALOG_CASES if case["id"] in {"patient-switch", "history-restore"}]
    factory, judge = RuntimeFactory(), Judge()
    report = await evaluator(factory=factory, judge=judge).run(dialog_cases=cases)
    assert not report.call_failures
    requests = [request for runtime in factory.records for request in runtime.orchestrator.calls]
    backgrounds = [json.loads(call[2]) for call in judge.calls]
    assert len(backgrounds) == len(requests) == 4
    for background, request in zip(backgrounds, requests):
        assert background["current_turn"]["identity"] == {
            "user_id": request.user_id, "patient_id": request.patient_id, "conv_id": request.conv_id}
        assert background["prior_context"] == (request.context or None)
    assert backgrounds[0]["current_turn"]["identity"]["patient_id"] == "patient_child"
    assert backgrounds[1]["current_turn"]["identity"]["patient_id"] == "patient_self"
    assert requests[0].conv_id not in judge.calls[1][2]
    assert requests[0].conv_id not in judge.calls[2][2]
    old_proposal = next(artifact for artifact in backgrounds[0]["current_turn"]["artifacts"]
                        if artifact["type"] == "appointment_proposal")
    assert old_proposal["data"]["proposal_id"] not in judge.calls[1][2]


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", ["no-slots", "retrieval-failure"])
async def test_judge_background_retains_business_failures_without_inventing_facts(case_id):
    case = next(case for case in DEFAULT_BOUNDARY_CASES if case["id"] == case_id)
    judge = Judge()
    report = await evaluator(factory=RuntimeFactory(), judge=judge).run(dialog_cases=[case])
    assert len(judge.calls) == 1 and not report.call_failures
    current = json.loads(judge.calls[0][2])["current_turn"]
    assert current["artifacts"] == report.results[0].metadata["artifacts"]
    assert current["tool_traces"] == report.results[0].metadata["tool_traces"]
    assert len(current["tool_traces"]) == 1 and current["tool_traces"][0]["success"] is False
    if case_id == "no-slots":
        assert current["tool_traces"][0]["error_code"] == "no_slots"
        assert len(current["artifacts"]) == 1
        assert current["artifacts"][0]["type"] == "slot_list"
        assert current["artifacts"][0]["data"]["slots"] == []
    else:
        assert current["tool_traces"][0]["error_code"] == "retrieval_failed"
        assert current["tool_traces"][0].get("sources", []) == []
        assert current["artifacts"] == []
    assert report.results[0].metadata["judge_context"] == judge.calls[0][2]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ['{}', '{"relevance": NaN, "accuracy":1,"completeness":1,"helpfulness":1}',
                                  '{"relevance":2,"accuracy":1,"completeness":1,"helpfulness":1}',
                                  '{"relevance":true,"accuracy":1,"completeness":1,"helpfulness":1}', 'not json'])
async def test_judge_rejects_missing_invalid_and_out_of_range_scores(raw):
    client = Model()
    client.reply = raw
    result = await LLMJudge(client, "fake").judge("问题", "答案")
    assert result.judge_failed and result.error


@pytest.mark.asyncio
async def test_judge_preserves_four_dimensions_and_mean():
    client = Model()
    client.reply = '{"relevance":0.9,"accuracy":0.8,"completeness":0.7,"helpfulness":0.6}'
    result = await LLMJudge(client, "fake").judge("问题", "答案", "事实背景")
    assert not result.judge_failed and result.overall == pytest.approx(.75)
    assert result.accuracy == .8 and result.helpfulness == .6
    assert client.calls[0]["temperature"] == 0 and client.calls[0]["max_tokens"] == 256


@pytest.mark.asyncio
async def test_all_fixed_scenarios_run_real_business_assertions_without_judge_for_boundaries():
    factory, judge = RuntimeFactory(), Judge()
    engine = evaluator(factory=factory, judge=judge)
    report = await engine.run(intent_cases=DEFAULT_INTENT_CASES, dialog_cases=DEFAULT_DIALOG_CASES, boundary_cases=DEFAULT_BOUNDARY_CASES)
    failed = [(result.test_id, result.detail, result.metadata.get("assertions")) for result in report.results if not result.passed]
    assert not failed, failed
    assert len(factory.records) == len(factory.closed) == 24
    assert len({runtime.visit_store.prefix for runtime in factory.records}) == 24
    assert len(judge.calls) == 24  # 12 个两轮聊天，12 个边界不用 Judge。
    business = [result for result in report.results if result.metadata.get("kind") == "business"]
    assert len(business) == 24 and all(result.scores == {} for result in business)
    assert report.metadata["case_counts"] == {"intent": 54, "dialog": 12, "boundary": 12}
    race = next(result for result in business if result.test_id == "last-slot-race:business")
    assert race.metadata["facts"]["parallel_confirm.executions"] == 1
    assert len(race.metadata["facts"]["parallel_confirm.results"]) == 2
    emergency = next(runtime for runtime in factory.records if runtime.orchestrator.case["id"] == "emergency-before-model")
    assert emergency.orchestrator.model_calls == 0 and emergency.memory_manager._client.calls == []
    assert emergency.orchestrator.calls[0].context == "" and emergency.orchestrator.calls[0].history is None


@pytest.mark.asyncio
async def test_patient_switch_and_new_cases_never_inherit_another_visit_history():
    factory = RuntimeFactory()
    cases = [case for case in DEFAULT_DIALOG_CASES if case["id"] in {"patient-switch", "history-restore"}]
    report = await evaluator(factory=factory).run(dialog_cases=cases)
    assert not report.call_failures
    switched, restored = factory.records
    old, new = switched.orchestrator.calls
    assert (old.patient_id, new.patient_id) == ("patient_child", "patient_self")
    assert old.conv_id != new.conv_id and new.history is None
    first, second = restored.orchestrator.calls
    assert first.conv_id == second.conv_id and second.history[0]["content"] == first.message
    assert first.conv_id not in {old.conv_id, new.conv_id}
    full = await restored.visit_store.get_messages(first.user_id, first.conv_id)
    assert len(full) == 4
    child_identity = VisitIdentity(user_id=old.user_id, patient_id=old.patient_id, conv_id=old.conv_id)
    self_identity = VisitIdentity(user_id=new.user_id, patient_id=new.patient_id, conv_id=new.conv_id)
    assert len((await switched.hospital_service.get_appointments(child_identity)).data["items"]) == 1
    assert (await switched.hospital_service.get_appointments(self_identity)).data["items"] == []


@pytest.mark.asyncio
async def test_patient_switch_without_source_selection_and_collaboration_split_across_turns_fail():
    patient_case = next(case for case in DEFAULT_DIALOG_CASES if case["id"] == "patient-switch")
    report = await evaluator(factory=RuntimeFactory(crash=True)).run(dialog_cases=[patient_case])
    assert not report.results[-1].passed
    checks = report.results[-1].metadata["assertions"]
    assert any(item["assertion"] == "source.selection.nonempty=true" and item["status"] == "failed" for item in checks)

    factory = RuntimeFactory()
    @asynccontextmanager
    async def split_factory(case, run_id):
        async with factory(case, run_id) as runtime:
            original = runtime.orchestrator.run
            async def split(req):
                result = await original(req)
                first = len(runtime.orchestrator.calls) == 1
                result.agent_types = [AgentType.APPOINTMENT if first else AgentType.GUIDANCE]
                result.artifacts = [artifact for artifact in result.artifacts if artifact.type == "slot_list"] if first else runtime.hospital_service.get_visit_checklist().artifacts
                return result
            runtime.orchestrator.run = split
            yield runtime
    case = next(case for case in DEFAULT_DIALOG_CASES if case["id"] == "collaboration")
    report = await evaluator(factory=split_factory).run(dialog_cases=[case])
    assert not report.results[-1].passed
    assert report.results[-1].metadata["facts"]["agents.include"] == ["appointment"]
    assert "visit_checklist" not in report.results[-1].metadata["facts"]["has_artifacts"]


@pytest.mark.asyncio
async def test_confirmation_action_persists_receipt_and_next_turn_recovers_it():
    case = deepcopy(next(case for case in DEFAULT_DIALOG_CASES if case["id"] == "create-confirm"))
    case.update(id="confirm-followup", turns=[*case["turns"], "确认后的记录"], between_turns={"2": case.pop("after_turns")})
    factory = RuntimeFactory()
    report = await evaluator(factory=factory).run(dialog_cases=[case])
    assert not report.call_failures
    runtime = factory.records[0]
    req = runtime.orchestrator.calls[-1]
    history = await runtime.visit_store.get_messages(req.user_id, req.conv_id)
    receipt = next(message for message in history if message.kind == "operation_result")
    assert receipt.receipt_id == receipt.metadata["receipt"]["receipt_id"]
    assert any("操作已完成" in message["content"] for message in req.history)


@pytest.mark.asyncio
async def test_unavailable_faults_and_nonisolated_runtime_are_not_passes():
    cases = [case for case in DEFAULT_BOUNDARY_CASES if case["id"] in {"retrieval-failure", "emergency-before-model"}]
    report = await evaluator(factory=RuntimeFactory(faults=False)).run(boundary_cases=cases)
    assert len(report.skipped) == 2 and report.passed == 0 and not report.avg_scores
    factory = RuntimeFactory(isolated=False)
    report = await evaluator(factory=factory).run(boundary_cases=[DEFAULT_BOUNDARY_CASES[1]])
    assert report.skipped and factory.records == factory.closed
    no_factory = await evaluator().run(boundary_cases=DEFAULT_BOUNDARY_CASES)
    assert len(no_factory.skipped) == 12 and no_factory.passed == 0


@pytest.mark.asyncio
async def test_failed_assertions_never_pass_on_high_judge_and_unknown_assertions_stay_unverified():
    case = deepcopy(DEFAULT_DIALOG_CASES[0])
    case["assertions"] = ["appointments.count=99", "unknown.fact=true"]
    report = await evaluator(factory=RuntimeFactory(), judge=Judge([1, 1])).run(dialog_cases=[case])
    assert report.results[0].passed and report.results[1].passed
    result = report.results[-1]
    assert not result.passed and result.scores == {}
    assert [item["status"] for item in result.metadata["assertions"]] == ["failed", "skipped"]
    assert report.metadata["business_failures"] == [result.test_id]
    assert not any("所有已测指标均达标" in recommendation for recommendation in report.recommendations)


@pytest.mark.asyncio
async def test_runtime_released_on_model_error_and_actions_cannot_be_chat_turns():
    factory = RuntimeFactory(crash=True)
    report = await evaluator(factory=factory).run(dialog_cases=[{"question": "调用失败"}])
    assert report.call_failures and factory.records == factory.closed
    report = await evaluator().run(dialog_cases=[{"turns": [{"action": "confirm_current"}]}])
    assert report.call_failures and report.passed == 0


@pytest.mark.asyncio
async def test_candidate_reports_do_not_override_reviewed_baseline_or_use_previous_candidate(tmp_path):
    path = tmp_path / "accepted.json"
    engine = evaluator(baseline_path=str(path))
    first = await engine.run(dialog_cases=[{"question": "资料"}])
    assert not path.exists() and Path(first.candidate_path).exists()
    with pytest.raises(ValueError, match="复核人"):
        engine.accept_baseline(first, reviewed_by=" ")
    engine.accept_baseline(first, reviewed_by="test-reviewer")
    accepted_bytes = path.read_bytes()
    engine._judge = Judge([.5, .85])
    second = await engine.run(dialog_cases=[{"question": "低分"}])
    third = await engine.run(dialog_cases=[{"question": "恢复部分"}])
    assert path.read_bytes() == accepted_bytes
    assert len(second.regressions) == len(third.regressions) == 4
    assert first.accepted_by is None and first.metadata["baseline_status"] == "candidate"
    assert len(list((tmp_path / "candidates").glob("*.json"))) == 3
    reloaded = evaluator(baseline_path=str(path))
    assert reloaded._baseline.accepted_by == "test-reviewer"
    assert reloaded._detect_regressions({"relevance": .85})
    # 历史自动保存文件没有人工接受标识，不自动升级成正式基线。
    old = json.loads(path.read_text(encoding="utf-8"))
    old.pop("accepted_by")
    path.write_text(json.dumps(old), encoding="utf-8")
    assert evaluator(baseline_path=str(path))._baseline is None


@pytest.mark.asyncio
async def test_monitor_retains_routing_penalties_tool_failure_and_latency_stats():
    agent_stats = {"appointment:0": {"success_rate": .7, "avg_ms": 6000, "total": 20, "routing_score": .4}}
    tool_stats = {"knowledge_search": {"success_rate": .5, "avg_latency_ms": 7000, "consecutive_fails": 3, "circuit_state": "open"}}
    penalties = {}
    monitor = PerformanceMonitor(SimpleNamespace(get_stats=lambda: agent_stats, update_routing_penalties=penalties.update),
                                 SimpleNamespace(get_stats=lambda: tool_stats))
    await monitor._collect()
    summary = monitor.summary()
    assert penalties["appointment:0"] == pytest.approx(.7)
    assert summary["tool_stats"] == tool_stats
    assert {alert["metric"] for alert in summary["active_alerts"]} == {
        "agent_success_rate:appointment:0", "agent_avg_ms:appointment:0",
        "tool_success_rate:knowledge_search", "tool_avg_ms:knowledge_search"}
    assert any("连续失败" in suggestion["title"] for suggestion in summary["suggestions"])
