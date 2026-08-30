from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from medipet.agent.runtime import LangGraphAgentRuntime
from medipet.assistant import MediPetAssistant
from medipet.benchmark import build_report, main
from medipet.contracts import TurnCommand
from medipet.model.port import ModelChunk, ModelPort, ModelRequest
from medipet.persistence.conversation import (
    DevelopmentVisitMatter,
    InMemoryVisitConversationStore,
)
from medipet.run_audit_http import run_audit_router
from medipet.run_audits import InMemoryRunAuditStore
from medipet.run_metrics import InMemoryRunMetricStore, RunMetric, RunMetricsRecorder
from medipet.skills.registry import InMemorySkillRegistry
from medipet.tools.registry import StaticToolProvider


class UsageModel(ModelPort):
    async def stream(self, request: ModelRequest):
        del request
        yield ModelChunk(text="测试", input_tokens=11)
        yield ModelChunk(text="完成", output_tokens=7)


@pytest.mark.asyncio
async def test_turn_metrics_capture_labels_usage_steps_and_terminal_outcome() -> None:
    store = InMemoryVisitConversationStore()
    await store.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id="fictional-patient",
            patient_display_name="虚构患者",
            participant_id="fictional-participant",
            participant_display_name="虚构参与者",
            visit_matter_id="fictional-visit",
            visit_matter_title="虚构测试",
        )
    )
    metrics = InMemoryRunMetricStore()
    assistant = MediPetAssistant(
        LangGraphAgentRuntime(UsageModel(), profile_version="profile-7"),
        store,
        provider="provider-a",
        model="model-a",
        profile_version="profile-7",
        metric_store=metrics,
    )

    events = [
        event
        async for event in assistant.handle_turn(
            TurnCommand(
                visit_matter_id="fictional-visit",
                participant_id="fictional-participant",
                idempotency_key="turn-1",
                message="虚构输入",
            )
        )
    ]
    metric = (await metrics.list_metrics())[0]

    assert events[-1].kind == "completed"
    assert (metric.provider, metric.model, metric.profile_version) == (
        "provider-a",
        "model-a",
        "profile-7",
    )
    assert metric.first_token_ms is not None
    assert metric.model_requests == 1
    assert metric.agent_steps == 1
    assert (metric.input_tokens, metric.output_tokens) == (11, 7)
    assert metric.outcome == "completed"


def test_recorder_separates_model_and_tool_time_deterministically() -> None:
    times = iter((1.0, 1.1, 1.3, 1.4, 1.7, 2.0, 2.0))
    recorder = RunMetricsRecorder(
        provider="fake", model="deterministic", profile_version="v1", clock=lambda: next(times)
    )
    model_started = recorder.begin_model_call()
    recorder.mark_first_token()
    recorder.end_model_call(model_started, input_tokens=3, output_tokens=2)
    tool_started = recorder.begin_tool_call()
    recorder.end_tool_call(tool_started)
    metric = recorder.finish("completed")

    assert metric.first_token_ms == pytest.approx(300)
    assert metric.model_ms == pytest.approx(300)
    assert metric.tool_ms == pytest.approx(300)
    assert metric.total_ms == pytest.approx(1000)
    assert metric.agent_steps == 2


@pytest.mark.asyncio
async def test_management_metrics_response_contains_only_deidentified_fields() -> None:
    audits = InMemoryRunAuditStore()
    metrics = InMemoryRunMetricStore()
    await metrics.record_metric(_metric())
    app = FastAPI()
    app.include_router(run_audit_router(audits, "secret", metrics))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/v1/admin/run-metrics", headers={"Authorization": "Bearer secret"}
        )

    payload = response.json()
    serialized = response.text
    assert response.status_code == 200
    assert set(payload["metrics"][0]) == {
        "provider",
        "model",
        "profileVersion",
        "firstTokenMs",
        "totalMs",
        "modelMs",
        "toolMs",
        "modelRequests",
        "inputTokens",
        "outputTokens",
        "agentSteps",
        "outcome",
        "createdAt",
    }
    assert "participant" not in serialized
    assert "prompt" not in serialized.lower()
    assert "database" not in serialized.lower()


def test_benchmark_report_is_grouped_by_model_without_run_identifiers() -> None:
    report = build_report("fake", [_metric(), _metric()])

    assert report["provider"] == "fake"
    assert report["model"] == "deterministic-v1"
    assert report["iterations"] == 2
    assert "traceId" not in report
    assert "visitMatterId" not in report


def test_live_benchmark_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDIPET_BENCHMARK_DATABASE_URL", "postgresql://test.invalid/db")
    monkeypatch.delenv("MEDIPET_ENABLE_LIVE_BENCHMARK", raising=False)
    monkeypatch.setattr("sys.argv", ["medipet-benchmark", "live"])

    with pytest.raises(SystemExit, match="MEDIPET_ENABLE_LIVE_BENCHMARK=1"):
        main()


@pytest.mark.asyncio
async def test_default_deployment_has_no_business_skills_or_tools() -> None:
    assert await StaticToolProvider().tools() == ()
    assert await InMemorySkillRegistry().list_skills() == []


def _metric() -> RunMetric:
    return RunMetric(
        provider="fake",
        model="deterministic-v1",
        profile_version="benchmark-v1",
        first_token_ms=1.0,
        total_ms=2.0,
        model_ms=1.0,
        tool_ms=0.0,
        model_requests=1,
        input_tokens=12,
        output_tokens=6,
        agent_steps=1,
        outcome="completed",
        created_at=datetime.now(UTC),
    )
