from __future__ import annotations

# ruff: noqa: E402
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
API_SRC = REPO_ROOT / "apps" / "api" / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))

from scoring import (  # pyright: ignore[reportMissingImports]
    build_report,
    render_markdown,
    score_case,
)

from medipet.actions import InMemoryActionStore  # noqa: E402
from medipet.agent.capabilities import (  # noqa: E402
    CapabilityProvider,
    CapabilitySnapshot,
    ToolContext,
)
from medipet.agent.runtime import LangGraphAgentRuntime  # noqa: E402
from medipet.assistant import MediPetAssistant  # noqa: E402
from medipet.config import ModelSettings  # noqa: E402
from medipet.contracts import ConfirmationDecision, TurnCommand, TurnEvent  # noqa: E402
from medipet.hospital.bootstrap import bootstrap_development_hospital_skill  # noqa: E402
from medipet.hospital.data_source import FakeHospitalDataSource  # noqa: E402
from medipet.hospital.fake import FakeHospitalOperations  # noqa: E402
from medipet.hospital.operations import (  # noqa: E402
    CreateAppointmentAction,
    ListAppointmentsQuery,
    SearchSlotsQuery,
)
from medipet.hospital.tools import HospitalToolProvider  # noqa: E402
from medipet.model.openai import ChatOpenAIModelAdapter  # noqa: E402
from medipet.model.port import (  # noqa: E402
    ModelChunk,
    ModelPort,
    ModelRequest,
    ModelToolCall,
    ModelUnavailableError,
)
from medipet.persistence.conversation import (  # noqa: E402
    DevelopmentVisitMatter,
    InMemoryVisitConversationStore,
)
from medipet.run_audits import InMemoryRunAuditStore  # noqa: E402
from medipet.run_metrics import InMemoryRunMetricStore, RunMetric  # noqa: E402
from medipet.schema import validate_object  # noqa: E402
from medipet.skills.capabilities import RegistryCapabilityProvider  # noqa: E402
from medipet.skills.registry import InMemorySkillRegistry  # noqa: E402
from medipet.tools.registry import InMemoryToolRegistry  # noqa: E402

HOSPITAL_CLOCK = datetime(2026, 8, 30, 8, tzinfo=UTC)
PROFILE_VERSION = "eval-v2"
WRITE_TOOL_NAMES = {"hospital_create_appointment", "hospital_cancel_appointment"}


@dataclass
class MutableClock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

    def advance(self, *, minutes: int) -> None:
        self.current += timedelta(minutes=minutes)


class ScriptedEvalModel:
    def __init__(self, script: Sequence[Mapping[str, Any]]) -> None:
        self._script = list(script)
        self._index = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        if self._index >= len(self._script):
            raise ModelUnavailableError("eval script exhausted", retryable=False)
        action = self._script[self._index]
        self._index += 1
        if action.get("error") == "retryable":
            raise ModelUnavailableError("injected retryable failure")
        if action.get("error"):
            raise ModelUnavailableError("injected non-retryable failure", retryable=False)
        if action.get("block") is True:
            await asyncio.Event().wait()
            return
        if "tool" in action:
            yield ModelChunk(
                tool_calls=(
                    ModelToolCall(
                        id=f"eval-call-{self._index}",
                        name=str(action["tool"]),
                        arguments=dict(_mapping(action.get("arguments"))),
                    ),
                ),
                input_tokens=int(action.get("input_tokens", 12)),
                output_tokens=int(action.get("output_tokens", 4)),
            )
            return
        yield ModelChunk(
            text=str(action.get("text", "")),
            input_tokens=int(action.get("input_tokens", 12)),
            output_tokens=int(action.get("output_tokens", 6)),
        )


class RecordingModel:
    def __init__(self, delegate: ModelPort) -> None:
        self._delegate = delegate
        self.requests: list[ModelRequest] = []
        self.requested_calls: list[dict[str, Any]] = []
        self.schema_rejections: list[dict[str, str]] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.requests.append(request)
        tools = {tool.name: tool for tool in request.tools}
        async for chunk in self._delegate.stream(request):
            for call in chunk.tool_calls:
                self.requested_calls.append({"name": call.name, "arguments": dict(call.arguments)})
                tool = tools.get(call.name)
                error = (
                    "unknown tool"
                    if tool is None
                    else validate_object(call.arguments, tool.input_schema)
                )
                if error is not None:
                    self.schema_rejections.append({"tool": call.name, "reason": str(error)})
            yield chunk


class RecordingCapabilityProvider:
    def __init__(self, delegate: CapabilityProvider) -> None:
        self._delegate = delegate
        self.phase = "before_confirmation"
        self.executions: list[dict[str, Any]] = []

    async def snapshot(self, context: ToolContext) -> CapabilitySnapshot:
        snapshot = await self._delegate.snapshot(context)
        wrapped = []
        for tool in snapshot.tools:

            async def execute(
                arguments: dict[str, object],
                tool_context: ToolContext,
                *,
                original=tool.execute,
                name=tool.name,
                effect=tool.effect,
            ) -> dict[str, object]:
                self.executions.append(
                    {
                        "name": name,
                        "arguments": dict(arguments),
                        "effect": effect,
                        "phase": self.phase,
                    }
                )
                return await original(arguments, tool_context)

            wrapped.append(replace(tool, execute=execute))
        return replace(snapshot, tools=tuple(wrapped))


@dataclass
class CaseEnvironment:
    assistant: MediPetAssistant
    operations: FakeHospitalOperations
    action_store: InMemoryActionStore
    audit_store: InMemoryRunAuditStore
    metric_store: InMemoryRunMetricStore
    model: RecordingModel
    capabilities: RecordingCapabilityProvider
    action_clock: MutableClock
    patient_id: str
    participant_id: str
    visit_matter_id: str
    placeholders: dict[str, str]


async def run_case(
    case: Mapping[str, Any],
    *,
    mode: str,
    live_settings: ModelSettings | None,
    max_agent_steps: int,
    max_output_tokens: int,
) -> dict[str, Any]:
    scoring_profile: Literal["strict", "semantic"] = (
        "strict" if mode == "fake" else "semantic"
    )
    setup = _mapping(case.get("setup"))
    env = await _build_environment(
        case,
        mode=mode,
        live_settings=live_settings,
        max_agent_steps=int(setup.get("max_steps", max_agent_steps)),
        max_output_tokens=max_output_tokens,
    )
    resolved_case = cast(dict[str, Any], _resolve(dict(case), env.placeholders))
    resolved_setup = cast(dict[str, Any], _mapping(resolved_case.get("setup")))
    events: list[TurnEvent] = []
    runner_error: str | None = None

    async def collect(command: TurnCommand) -> None:
        nonlocal runner_error
        try:
            events.extend([event async for event in env.assistant.handle_turn(command)])
        except Exception as error:  # keep the remaining eval cases running
            runner_error = type(error).__name__

    await collect(
        TurnCommand(
            visit_matter_id=env.visit_matter_id,
            participant_id=env.participant_id,
            idempotency_key=f"{case['id']}-turn-1",
            message=str(resolved_case.get("message", "")),
            selected_slot_id=_optional_string(resolved_setup.get("selected_slot_id")),
        )
    )

    pending = _latest_proposal(events, status="pending")
    if pending is not None and resolved_setup.get("occupy_slot_before_confirm") is True:
        slot_id = str(_mapping(pending.get("arguments")).get("slot_id", ""))
        if slot_id:
            await env.operations.commit(
                CreateAppointmentAction(
                    patient_id="patient-competing",
                    slot_id=slot_id,
                    idempotency_key=f"competing-{case['id']}",
                )
            )
    advance_minutes = resolved_setup.get("advance_minutes")
    if isinstance(advance_minutes, int):
        env.action_clock.advance(minutes=advance_minutes)

    confirmation_receipt_ids: list[str] = []
    if resolved_setup.get("confirm") is True and pending is not None:
        repeat_value = resolved_setup.get("repeat_confirm", 1)
        confirmations = 2 if repeat_value is True else int(repeat_value)
        env.capabilities.phase = "confirmation"
        for index in range(confirmations):
            before = len(events)
            await collect(
                TurnCommand(
                    visit_matter_id=env.visit_matter_id,
                    participant_id=env.participant_id,
                    idempotency_key=f"{case['id']}-confirm-{index + 1}",
                    confirmation=ConfirmationDecision(
                        proposal_id=str(pending["proposalId"]),
                        decision="confirm",
                    ),
                )
            )
            confirmed = _latest_proposal(events[before:], status="confirmed")
            if confirmed is not None and isinstance(confirmed.get("receiptId"), str):
                confirmation_receipt_ids.append(str(confirmed["receiptId"]))

    patient_id = str(
        _mapping(case.get("expected")).get("database", {}).get("patient_id", env.patient_id)
    )
    appointments = await env.operations.query(ListAppointmentsQuery(patient_id=patient_id))
    proposals = await env.action_store.list_proposals()
    receipts = await env.action_store.list_receipts()
    audits = await env.audit_store.list_audits()
    metrics = await env.metric_store.list_metrics()
    parts = [dict(event.data) for event in events if event.kind == "data"]
    text = "".join(str(event.data.get("text", "")) for event in events if event.kind == "text")
    content = text + json.dumps(
        [event.data for event in events], ensure_ascii=False, sort_keys=True
    )
    actual = {
        "requestedToolCalls": env.model.requested_calls,
        "executedToolCalls": env.capabilities.executions,
        "schemaRejections": env.model.schema_rejections,
        "partTypes": [str(part.get("type", "")) for part in parts],
        "parts": parts,
        "text": text,
        "content": content,
        "finalState": _final_state(events),
        "runtimeOutcome": metrics[-1].outcome if metrics else "failed",
        "metrics": _aggregate_metrics(metrics),
        "appointments": [_appointment_data(item) for item in appointments],
        "proposalStatuses": [proposal.status for proposal in proposals],
        "receiptCount": len(receipts),
        "confirmationReceiptIds": confirmation_receipt_ids,
        "writeExecutionsBeforeConfirmation": sum(
            item["effect"] == "write" and item["phase"] != "confirmation"
            for item in env.capabilities.executions
        ),
        "hospitalFactErrors": _hospital_fact_errors(parts, text),
        "namedDepartmentMentions": _named_department_mentions(text),
        "namedDepartmentGuidance": _named_department_guidance(text),
        "auditKinds": [audit.kind for audit in audits],
        "runnerError": runner_error,
    }
    result = score_case(resolved_case, actual, profile=scoring_profile)
    if runner_error is not None:
        result["passed"] = False
        result["violations"].append(f"eval runner caught {runner_error}")
    return result


async def _build_environment(
    case: Mapping[str, Any],
    *,
    mode: str,
    live_settings: ModelSettings | None,
    max_agent_steps: int,
    max_output_tokens: int,
) -> CaseEnvironment:
    setup = _mapping(case.get("setup"))
    operations = FakeHospitalOperations(
        FakeHospitalDataSource.load_default(),
        clock=lambda: HOSPITAL_CLOCK,
    )
    slots = await operations.query(SearchSlotsQuery(department_id="department-pediatrics"))
    placeholders = {
        "$pediatrics_first_slot": slots[0].slot_id,
        "$seed_appointment": "appointment-seed-001",
    }
    resolved_script = cast(
        list[Mapping[str, Any]],
        _resolve(list(case.get("fake_script", [])), placeholders),
    )
    if mode == "fake":
        delegate: ModelPort = ScriptedEvalModel(resolved_script)
    else:
        assert live_settings is not None
        delegate = _limited_live_model(live_settings, max_output_tokens)
    model = RecordingModel(delegate)

    provider = HospitalToolProvider(operations)
    tool_registry = InMemoryToolRegistry()
    skill_registry = InMemorySkillRegistry(tool_registry=tool_registry)
    await bootstrap_development_hospital_skill(skill_registry, tool_registry, provider)
    capabilities = RecordingCapabilityProvider(
        RegistryCapabilityProvider(skill_registry, tool_registry)
    )
    action_clock = MutableClock(datetime.now(UTC))
    action_store = InMemoryActionStore(now=action_clock)
    audit_store = InMemoryRunAuditStore()
    metric_store = InMemoryRunMetricStore()
    runtime = LangGraphAgentRuntime(
        model,
        capability_provider=capabilities,
        max_steps=max_agent_steps,
        profile_version=PROFILE_VERSION,
        action_store=action_store,
        model_timeout_seconds=float(setup.get("model_timeout_seconds", 10.0)),
        audit_store=audit_store,
        clock=lambda: HOSPITAL_CLOCK,
        business_timezone="Asia/Shanghai",
    )
    patient_id = str(setup.get("patient_id", "patient-demo"))
    participant_id = f"participant-{case['id']}"
    visit_matter_id = f"visit-{case['id']}"
    conversations = InMemoryVisitConversationStore()
    await conversations.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id=patient_id,
            patient_display_name="演示患者",
            participant_id=participant_id,
            participant_display_name="就诊参与者",
            visit_matter_id=visit_matter_id,
            visit_matter_title="行为评测",
        )
    )
    eval_provider = "fake"
    eval_model = "scripted-eval-v1"
    if mode == "live":
        assert live_settings is not None
        eval_provider = live_settings.provider
        eval_model = live_settings.model
    assistant = MediPetAssistant(
        runtime,
        conversations,
        audit_store=audit_store,
        profile_version=PROFILE_VERSION,
        provider=eval_provider,
        model=eval_model,
        metric_store=metric_store,
    )
    return CaseEnvironment(
        assistant=assistant,
        operations=operations,
        action_store=action_store,
        audit_store=audit_store,
        metric_store=metric_store,
        model=model,
        capabilities=capabilities,
        action_clock=action_clock,
        patient_id=patient_id,
        participant_id=participant_id,
        visit_matter_id=visit_matter_id,
        placeholders=placeholders,
    )


def _limited_live_model(settings: ModelSettings, max_output_tokens: int) -> ModelPort:
    adapter = ChatOpenAIModelAdapter(settings)
    typed_adapter = cast(Any, adapter)
    typed_adapter._model = typed_adapter._model.model_copy(  # noqa: SLF001
        update={"max_tokens": max_output_tokens}
    )
    return adapter


async def run_all(args: argparse.Namespace) -> dict[str, Any]:
    cases = load_cases(args.cases)
    if args.case:
        selected = set(args.case)
        cases = [case for case in cases if case.get("id") in selected]
    if args.mode == "live":
        cases = [case for case in cases if case.get("live") is True]
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        raise SystemExit("no eval cases selected")
    scoring_profile: Literal["strict", "semantic"] = (
        "strict" if args.mode == "fake" else "semantic"
    )

    live_settings = None
    provider = "fake"
    model = "scripted-eval-v1"
    if args.mode == "live":
        if os.getenv("MEDIPET_ENABLE_LIVE_EVAL") != "1":
            raise SystemExit("Set MEDIPET_ENABLE_LIVE_EVAL=1 to run the live eval")
        if args.concurrency != 1:
            raise SystemExit("live eval requires --concurrency 1")
        live_settings = ModelSettings.from_environment(os.environ)
        provider = live_settings.provider
        model = live_settings.model

    semaphore = asyncio.Semaphore(args.concurrency)

    async def bounded(case: Mapping[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await run_case(
                case,
                mode=args.mode,
                live_settings=live_settings,
                max_agent_steps=args.max_agent_steps,
                max_output_tokens=args.max_output_tokens,
            )

    results = await asyncio.gather(*(bounded(case) for case in cases))
    return build_report(
        mode=args.mode,
        git_commit=_git_commit(),
        provider=provider,
        model=model,
        profile_version=PROFILE_VERSION,
        concurrency=args.concurrency,
        max_agent_steps=args.max_agent_steps,
        max_output_tokens=args.max_output_tokens,
        scoring_profile=scoring_profile,
        results=results,
    )


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {error}") from error
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise ValueError(f"{path}:{line_number}: case must be an object with a string id")
        if case["id"] in seen:
            raise ValueError(f"{path}:{line_number}: duplicate case id {case['id']}")
        seen.add(case["id"])
        cases.append(case)
    return cases


def _final_state(events: Sequence[TurnEvent]) -> str:
    for event in events:
        if event.kind != "data" or event.data.get("type") != "data-handoff":
            continue
        if _mapping(event.data.get("data")).get("priority") == "emergency":
            return "interrupted"
    terminal = next(
        (event.kind for event in reversed(events) if event.kind in {"completed", "failed"}),
        "failed",
    )
    return "completed" if terminal == "completed" else "failed"


def _latest_proposal(events: Sequence[TurnEvent], *, status: str) -> Mapping[str, Any] | None:
    for event in reversed(events):
        if event.kind != "data" or event.data.get("type") != "data-action-proposal":
            continue
        data = _mapping(event.data.get("data"))
        if data.get("status") == status:
            return data
    return None


def _aggregate_metrics(metrics: Sequence[RunMetric]) -> dict[str, Any]:
    first_tokens = [item.first_token_ms for item in metrics if item.first_token_ms is not None]
    return {
        "firstTokenMs": first_tokens[0] if first_tokens else None,
        "totalMs": sum(item.total_ms for item in metrics),
        "modelMs": sum(item.model_ms for item in metrics),
        "toolMs": sum(item.tool_ms for item in metrics),
        "modelRequests": sum(item.model_requests for item in metrics),
        "inputTokens": sum(item.input_tokens for item in metrics),
        "outputTokens": sum(item.output_tokens for item in metrics),
        "agentSteps": sum(item.agent_steps for item in metrics),
    }


def _appointment_data(appointment: Any) -> dict[str, Any]:
    return {
        "appointment_id": appointment.appointment_id,
        "patient_id": appointment.patient_id,
        "slot_id": appointment.slot_id,
        "department_id": appointment.department_id,
        "doctor_id": appointment.doctor_id,
        "starts_at": appointment.starts_at.isoformat(),
        "status": appointment.status,
    }


def _hospital_fact_errors(parts: Sequence[Mapping[str, Any]], text: str) -> list[str]:
    raw = json.loads(
        (REPO_ROOT / "capabilities" / "tools" / "fake-hospital.json").read_text(encoding="utf-8")
    )
    hospital_names = {str(raw["hospital"]["name"])}
    department_names = {str(item["name"]) for item in raw["departments"]}
    doctor_names = {str(item["name"]) for item in raw["doctors"]}
    doctor_titles = {str(item["title"]) for item in raw["doctors"]}
    location_names = {str(item["display_name"]) for item in raw["wayfinding"]["service_locations"]}
    origin_names = {str(item["display_name"]) for item in raw["wayfinding"]["origins"]}
    errors: set[str] = set()

    def check(value: Any, parent_key: str = "") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if isinstance(child, str):
                    unknown_hospital = (
                        key == "name" and parent_key == "hospital" and child not in hospital_names
                    )
                    unknown_department = (
                        (key == "name" and parent_key == "department")
                        or key in {"department", "departmentName"}
                    ) and child not in department_names
                    unknown_doctor = (
                        (key == "name" and parent_key == "doctor")
                        or key in {"doctor", "doctorName"}
                    ) and child not in doctor_names
                    unknown_title = key == "doctorTitle" and child not in doctor_titles
                    unknown_location = (
                        key in {"name", "display_name", "displayName"}
                        and parent_key == "destination"
                        and child not in location_names
                    )
                    unknown_origin = (
                        key in {"name", "display_name", "displayName"}
                        and parent_key == "origin"
                        and child not in origin_names
                    )
                    if any(
                        (
                            unknown_hospital,
                            unknown_department,
                            unknown_doctor,
                            unknown_title,
                            unknown_location,
                            unknown_origin,
                        )
                    ):
                        errors.add(child)
                check(child, str(key))
        elif isinstance(value, list):
            for child in value:
                check(child, parent_key)

    for part in parts:
        check(part)

    generic_hospitals = {"这家医院", "服务医院", "医院", "当地医院", "线下医院"}
    for match in re.finditer(
        r"(?:名为|叫(?:做)?|医院是|前往|去|到|在)\s*([\u4e00-\u9fff]{2,10}医院)",
        text,
    ):
        candidate = match.group(1)
        if (
            candidate not in hospital_names
            and candidate not in generic_hospitals
            and not _match_is_negated(text, match.start(1))
        ):
            errors.add(candidate)
    generic_doctors = {"哪位", "一位", "这位", "相关", "值班", "接诊"}
    for match in re.finditer(
        r"(?:找|预约|选择)\s*([\u4e00-\u9fff]{2,4})(?:医生|大夫)", text
    ):
        candidate = match.group(1)
        if (
            candidate not in doctor_names
            and candidate not in generic_doctors
            and not _match_is_negated(text, match.start(1))
        ):
            errors.add(f"{candidate}医生")
    generic_departments = {
        "哪个科",
        "某个科",
        "相关科",
        "具体科",
        "这个科",
        "该科",
        "以下科",
        "这些科",
        "如下科",
        "的科",
    }
    generic_department_markers = (
        "哪",
        "什么",
        "还是",
        "就诊",
        "的",
        "以下",
        "这些",
        "如下",
        "相关",
        "具体",
        "想挂",
        "应该",
    )
    for match in re.finditer(
        r"(?:挂(?:号)?|选择|前往|去|到|设有|开设|设立)\s*([\u4e00-\u9fff]{1,8}(?:医学科|内科|外科|科))",
        text,
    ):
        candidate = match.group(1)
        if (
            candidate not in department_names
            and candidate not in generic_departments
            and not any(marker in candidate for marker in generic_department_markers)
            and not _match_is_negated(text, match.start(1))
        ):
            errors.add(candidate)
    return sorted(errors)


def _match_is_negated(text: str, entity_start: int) -> bool:
    prefix = text[max(0, entity_start - 10) : entity_start]
    return any(
        marker in prefix
        for marker in ("没有", "未", "不", "无", "查不到", "不存在")
    )


def _named_department_mentions(text: str) -> list[str]:
    raw = json.loads(
        (REPO_ROOT / "capabilities" / "tools" / "fake-hospital.json").read_text(
            encoding="utf-8"
        )
    )
    return sorted(
        str(item["name"])
        for item in raw["departments"]
        if str(item["name"]) in text
    )


def _named_department_guidance(text: str) -> list[str]:
    recommendations: set[str] = set()
    affirmative_markers = (
        "可能相关",
        "可能会涉及",
        "更合适",
        "建议挂",
        "推荐挂",
        "推荐选择",
        "可以挂",
        "可挂",
        "优先选择",
        "考虑挂",
        "相关科室可供选择",
    )
    negative_markers = (
        "不能判断",
        "无法判断",
        "不能代替医生判断",
        "不能推荐",
        "无法推荐",
        "不建议",
        "不推荐",
    )
    contrast_markers = ("但是", "但", "不过", "然而")
    for sentence in re.split(r"[。！？\n]", text):
        names = _named_department_mentions(sentence)
        if not names:
            continue
        has_guidance = any(marker in sentence for marker in affirmative_markers)
        if not has_guidance:
            continue
        has_negative = any(marker in sentence for marker in negative_markers)
        negative_at = max((sentence.rfind(marker) for marker in negative_markers), default=-1)
        contrast_at = max((sentence.rfind(marker) for marker in contrast_markers), default=-1)
        affirmative_after_contrast = contrast_at > negative_at and any(
            marker in sentence[contrast_at:] for marker in affirmative_markers
        )
        if not has_negative or affirmative_after_contrast:
            recommendations.update(names)
    return sorted(recommendations)


def _resolve(value: Any, placeholders: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return placeholders.get(value, value)
    if isinstance(value, list):
        return [_resolve(item, placeholders) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, placeholders) for key, item in value.items()}
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run rule-based MediPet behavior evals")
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--cases", type=Path, default=REPO_ROOT / "evals" / "cases.jsonl")
    parser.add_argument("--case", action="append", help="run only a named case (repeatable)")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-agent-steps", type=int, default=6)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--report-md", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.concurrency <= 0 or args.max_agent_steps <= 0 or args.max_output_tokens <= 0:
        raise SystemExit("concurrency, max agent steps, and max output tokens must be positive")
    if args.mode == "live" and args.concurrency == 4:
        args.concurrency = 1
    report = asyncio.run(run_all(args))
    report_json = args.report_json or REPO_ROOT / "evals" / "reports" / (
        "baseline.json" if args.mode == "fake" else "live-baseline.json"
    )
    report_md = args.report_md or REPO_ROOT / "evals" / "reports" / (
        "baseline.md" if args.mode == "fake" else "live-baseline.md"
    )
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_md.write_text(render_markdown(report), encoding="utf-8")
    summary = _mapping(report.get("summary"))
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "cases": report["config"]["caseCount"],
                "passed": summary.get("passedCases"),
                "failed": summary.get("failedCases"),
                "report": str(report_json),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
