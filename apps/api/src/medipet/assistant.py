from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from uuid import uuid4

from medipet.agent.capabilities import ToolContext
from medipet.agent.runtime import AgentRequest, AgentRuntime
from medipet.contracts import TurnCommand, TurnEvent
from medipet.emergency import emergency_interruption_for
from medipet.hospital.selection import resolve_wayfinding_selection
from medipet.model.port import ModelMessage
from medipet.persistence.conversation import (
    StoredMessage,
    TerminalMessageState,
    VisitConversationStore,
    VisitMatterArchivedError,
    VisitMatterNotFoundError,
    VisitTurn,
)
from medipet.run_audits import NullRunAuditStore, RunAuditContext, RunAuditStore
from medipet.run_metrics import (
    NullRunMetricStore,
    RunMetricsRecorder,
    RunMetricStore,
    TerminalOutcome,
)
from medipet.triage import DepartmentBoundaryResult, evaluate_department_boundary

LOGGER = logging.getLogger(__name__)


class MediPetAssistant:
    def __init__(
        self,
        agent_runtime: AgentRuntime,
        conversation_store: VisitConversationStore,
        *,
        context_message_limit: int = 20,
        persistence_batch_characters: int = 256,
        turn_timeout_seconds: float | None = None,
        audit_store: RunAuditStore | None = None,
        profile_version: str = "static",
        provider: str = "unknown",
        model: str = "unknown",
        metric_store: RunMetricStore | None = None,
    ) -> None:
        self._agent_runtime = agent_runtime
        self._conversation_store = conversation_store
        self._context_message_limit = context_message_limit
        self._persistence_batch_characters = persistence_batch_characters
        self._turn_timeout_seconds = turn_timeout_seconds
        self._audit_store = audit_store or NullRunAuditStore()
        self._profile_version = profile_version
        self._provider = provider
        self._model = model
        self._metric_store = metric_store or NullRunMetricStore()

    async def handle_turn(self, command: TurnCommand) -> AsyncGenerator[TurnEvent, None]:
        trace_id = f"trace-{uuid4().hex[:12]}"
        metrics = RunMetricsRecorder(
            provider=self._provider,
            model=self._model,
            profile_version=self._profile_version,
        )
        outcome: TerminalOutcome = "failed"
        audit_context = RunAuditContext(
            trace_id=trace_id,
            visit_matter_id=command.visit_matter_id,
            turn_id=command.idempotency_key,
            profile_version=self._profile_version,
        )
        try:
            async with aclosing(self._handle_turn(command, audit_context, metrics)) as events:
                if self._turn_timeout_seconds is None:
                    async for event in events:
                        if event.kind == "completed":
                            outcome = "completed"
                        yield event
                    return
                async with asyncio.timeout(self._turn_timeout_seconds):
                    async for event in events:
                        if event.kind == "completed":
                            outcome = "completed"
                        yield event
        except TimeoutError:
            outcome = "turn_timeout"
            await self._audit_store.record("turn_timeout", audit_context)
            yield TurnEvent(
                kind="failed",
                data={"message": "本次协助已超时，请重试。", "traceId": trace_id},
            )
        except VisitMatterArchivedError as error:
            outcome = "failed"
            await self._audit_store.record("failed", audit_context)
            yield TurnEvent(
                kind="failed",
                data={"message": str(error), "traceId": trace_id},
            )
        except (asyncio.CancelledError, GeneratorExit):
            outcome = "cancelled"
            raise
        finally:
            try:
                await self._metric_store.record_metric(metrics.finish(outcome))
            except Exception:
                LOGGER.warning("run_metric_record_failed")

    async def _handle_turn(
        self,
        command: TurnCommand,
        audit_context: RunAuditContext,
        metrics: RunMetricsRecorder,
    ) -> AsyncGenerator[TurnEvent, None]:
        trace_id = audit_context.trace_id

        if command.confirmation is not None:
            try:
                visit_context = await self._conversation_store.visit_context(
                    command.visit_matter_id,
                    command.participant_id,
                )
            except VisitMatterNotFoundError as error:
                await self._audit_store.record("failed", audit_context)
                yield TurnEvent(
                    kind="failed",
                    data={"message": str(error), "traceId": trace_id},
                )
                return
            proposal_visible = await self._conversation_store.has_action_proposal_part(
                command.visit_matter_id,
                command.confirmation.proposal_id,
            )
            event = await self._agent_runtime.decide(
                command.confirmation.proposal_id,
                command.confirmation.decision,
                ToolContext(
                    visit_matter_id=command.visit_matter_id,
                    participant_id=command.participant_id,
                    idempotency_key=command.idempotency_key,
                    visit_stage=visit_context.visit_stage,
                    patient_id=visit_context.patient_id,
                    patient_display_name=visit_context.patient_display_name,
                ),
                proposal_visible=proposal_visible,
            )
            if event.kind == "data":
                try:
                    await self._conversation_store.update_action_proposal_part(
                        command.visit_matter_id,
                        command.confirmation.proposal_id,
                        event.data,
                    )
                except Exception:
                    LOGGER.warning(
                        "action_proposal_history_update_failed",
                        exc_info=True,
                    )
            await self._audit_store.record(
                "failed" if event.kind == "failed" else "completed",
                audit_context,
            )
            yield TurnEvent(kind=event.kind, data={**event.data, "traceId": trace_id})
            if event.kind != "failed":
                yield TurnEvent(kind="completed", data={"traceId": trace_id})
            return

        if command.message is None or not command.message.strip():
            await self._audit_store.record("failed", audit_context)
            yield TurnEvent(
                kind="failed",
                data={"message": "缺少就诊参与者消息", "traceId": trace_id},
            )
            return

        turn = VisitTurn(
            visit_matter_id=command.visit_matter_id,
            participant_id=command.participant_id,
            turn_id=command.idempotency_key,
        )
        await self._conversation_store.add_participant_message(
            turn=turn,
            content=command.message.strip(),
            selected_slot_id=command.selected_slot_id,
        )
        assistant_message, claimed = await self._conversation_store.claim_assistant_message(
            turn=turn,
        )
        if not claimed:
            if assistant_message.state == "completed":
                for part in assistant_message.parts:
                    yield TurnEvent(kind="data", data=part)
                if assistant_message.content:
                    yield TurnEvent(
                        kind="text",
                        data={"text": assistant_message.content},
                    )
                await self._audit_store.record("completed", audit_context)
                yield TurnEvent(kind="completed", data={"traceId": trace_id})
                return
            await self._audit_store.record("failed", audit_context)
            yield TurnEvent(
                kind="failed",
                data={
                    "message": "该请求已在处理中或已结束，请稍后刷新聊天记录。",
                    "traceId": trace_id,
                },
            )
            return
        buffered_text: list[str] = []
        buffered_characters = 0
        streaming_started = False
        terminal = False

        async def flush_text() -> None:
            nonlocal buffered_characters
            if not buffered_text:
                return
            await self._conversation_store.append_assistant_text(
                assistant_message.id,
                "".join(buffered_text),
            )
            buffered_text.clear()
            buffered_characters = 0

        async def finalize(state: TerminalMessageState) -> None:
            nonlocal terminal
            await flush_text()
            if terminal:
                return
            await self._conversation_store.finish_assistant_message(
                assistant_message.id,
                state,
            )
            terminal = True
            await self._audit_store.record(state, audit_context)

        try:
            emergency_handoff = emergency_interruption_for(command.message)
            if emergency_handoff is not None:
                typed_handoff_part = emergency_handoff.message_part()
                handoff_part: dict[str, object] = dict(typed_handoff_part)
                await self._conversation_store.append_assistant_part(
                    assistant_message.id,
                    handoff_part,
                )
                await self._conversation_store.mark_assistant_streaming(
                    assistant_message.id
                )
                await finalize("completed")
                yield TurnEvent(kind="data", data=handoff_part)
                yield TurnEvent(kind="completed", data={"traceId": trace_id})
                return

            completed_history = await self._conversation_store.list_completed_messages(
                command.visit_matter_id,
                limit=max(self._context_message_limit, 3),
            )
            boundary_decision = evaluate_department_boundary(
                current_message=command.message,
                previous_participant_message=_previous_participant_message(
                    completed_history,
                    current_turn_id=command.idempotency_key,
                ),
                has_selected_slot=command.selected_slot_id is not None,
            )
            if boundary_decision.result is DepartmentBoundaryResult.MANUAL_TRIAGE_REQUIRED:
                assert boundary_decision.response is not None
                await self._conversation_store.mark_assistant_streaming(
                    assistant_message.id
                )
                await self._conversation_store.append_assistant_text(
                    assistant_message.id,
                    boundary_decision.response,
                )
                await finalize("completed")
                yield TurnEvent(kind="text", data={"text": boundary_decision.response})
                yield TurnEvent(kind="completed", data={"traceId": trace_id})
                return

            agent_history = (
                completed_history[-self._context_message_limit :]
                if self._context_message_limit > 0
                else []
            )
            visit_context = await self._conversation_store.visit_context(
                command.visit_matter_id, command.participant_id
            )
            request = AgentRequest(
                messages=tuple(
                    ModelMessage(
                        role="user" if message.role == "participant" else "assistant",
                        content=(
                            _selected_slot_message(message.content, command.selected_slot_id)
                            if message.role == "participant"
                            and message.turn_id == command.idempotency_key
                            and command.selected_slot_id is not None
                            else _model_message_content(message.content, message.parts)
                        ),
                    )
                    for message in agent_history
                ),
                context=ToolContext(
                    visit_matter_id=command.visit_matter_id,
                    participant_id=command.participant_id,
                    idempotency_key=command.idempotency_key,
                    visit_stage=visit_context.visit_stage,
                    patient_id=visit_context.patient_id,
                    patient_display_name=visit_context.patient_display_name,
                    participant_tool_selection=resolve_wayfinding_selection(
                        _unresolved_wayfinding_messages(agent_history)
                    ),
                ),
                trace_id=trace_id,
                metrics=metrics,
            )
            async with aclosing(self._agent_runtime.run(request)) as runtime_events:
                async for event in runtime_events:
                    if event.kind == "text":
                        if not streaming_started:
                            await self._conversation_store.mark_assistant_streaming(
                                assistant_message.id
                            )
                            streaming_started = True
                        text = event.data["text"]
                        buffered_text.append(text)
                        buffered_characters += len(text)
                        if buffered_characters >= self._persistence_batch_characters:
                            await flush_text()

                    if event.kind == "failed":
                        await finalize("failed")
                        yield TurnEvent(
                            kind="failed",
                            data={**event.data, "traceId": trace_id},
                        )
                        return
                    if event.kind == "data":
                        await self._conversation_store.append_assistant_part(
                            assistant_message.id,
                            event.data,
                        )
                    yield TurnEvent(kind=event.kind, data=event.data)

            if not streaming_started:
                await self._conversation_store.mark_assistant_streaming(assistant_message.id)
            await finalize("completed")
            yield TurnEvent(kind="completed", data={"traceId": trace_id})
        except (asyncio.CancelledError, GeneratorExit):
            await finalize("cancelled")
            raise
        except Exception:
            await finalize("failed")
            raise


def _selected_slot_message(content: str, slot_id: str) -> str:
    return (
        f"{content}\n\n"
        "[本轮界面已选择号源；准确的 slot_id 为 "
        f"{slot_id}。这只代表参与者的选择，仍须通过医院 Tool 生成权威确认。]"
    )


def _previous_participant_message(
    messages: list[StoredMessage],
    *,
    current_turn_id: str,
) -> str | None:
    return next(
        (
            message.content
            for message in reversed(messages)
            if message.role == "participant" and message.turn_id != current_turn_id
        ),
        None,
    )


def _unresolved_wayfinding_messages(
    messages: list[StoredMessage],
) -> tuple[str, ...]:
    start = 0
    for index, message in enumerate(messages):
        if message.role != "assistant":
            continue
        for part in message.parts:
            part_type = part.get("type")
            if part_type == "data-hospital-wayfinding":
                start = index + 1
                break
            if part_type == "data-hospital-wayfinding-unavailable":
                data = part.get("data")
                if isinstance(data, dict) and data.get("reason") != "selection_required":
                    start = index + 1
                    break
    return tuple(
        message.content
        for message in messages[start:]
        if message.role == "participant"
    )


def _model_message_content(
    content: str,
    parts: tuple[dict[str, object], ...],
) -> str:
    slot_parts = [part for part in parts if part.get("type") == "data-slot-options"]
    if not slot_parts:
        return content
    verified = json.dumps(slot_parts, ensure_ascii=False, separators=(",", ":"))
    prefix = f"{content}\n\n" if content else ""
    return f"{prefix}[此前由医院 Tool 验证并展示的号源，顺序保持不变：{verified}]"
