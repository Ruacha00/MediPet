from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from uuid import uuid4

from medipet.agent.capabilities import ToolContext
from medipet.agent.runtime import AgentRequest, AgentRuntime
from medipet.contracts import TurnCommand, TurnEvent
from medipet.model.port import ModelMessage
from medipet.persistence.conversation import (
    TerminalMessageState,
    VisitConversationStore,
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
                visit_stage = await self._conversation_store.visit_stage(
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
            event = await self._agent_runtime.decide(
                command.confirmation.proposal_id,
                command.confirmation.decision,
                ToolContext(
                    visit_matter_id=command.visit_matter_id,
                    participant_id=command.participant_id,
                    idempotency_key=command.idempotency_key,
                    visit_stage=visit_stage,
                ),
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
        )
        assistant_message = await self._conversation_store.add_assistant_message(
            turn=turn,
        )
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
            completed_history = await self._conversation_store.list_completed_messages(
                command.visit_matter_id,
                limit=self._context_message_limit,
            )
            visit_stage = await self._conversation_store.visit_stage(
                command.visit_matter_id, command.participant_id
            )
            request = AgentRequest(
                messages=tuple(
                    ModelMessage(
                        role="user" if message.role == "participant" else "assistant",
                        content=message.content,
                    )
                    for message in completed_history
                ),
                context=ToolContext(
                    visit_matter_id=command.visit_matter_id,
                    participant_id=command.participant_id,
                    idempotency_key=command.idempotency_key,
                    visit_stage=visit_stage,
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
