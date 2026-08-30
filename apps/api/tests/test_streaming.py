from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

from medipet.agent.runtime import LangGraphAgentRuntime
from medipet.assistant import MediPetAssistant
from medipet.contracts import TurnCommand, TurnEvent
from medipet.delivery.streaming import to_ui_message_stream
from medipet.model.port import ModelChunk, ModelPort, ModelRequest
from medipet.persistence.conversation import (
    DevelopmentVisitMatter,
    InMemoryVisitConversationStore,
)


@pytest.mark.asyncio
async def test_closing_browser_stream_closes_the_upstream_turn() -> None:
    closed = False

    async def turn_events() -> AsyncGenerator[TurnEvent, None]:
        nonlocal closed
        try:
            yield TurnEvent(kind="status", data={"label": "working"})
            yield TurnEvent(kind="text", data={"text": "partial"})
            yield TurnEvent(kind="completed")
        finally:
            closed = True

    stream = to_ui_message_stream(turn_events())
    _ = await anext(stream)
    _ = await anext(stream)
    await stream.aclose()

    assert closed is True


class BlockingAfterTextModel(ModelPort):
    def __init__(self) -> None:
        self.released = asyncio.Event()

    async def stream(self, request: ModelRequest) -> AsyncGenerator[ModelChunk, None]:
        del request
        try:
            yield ModelChunk(text="partial")
            await asyncio.Event().wait()
        finally:
            self.released.set()


@pytest.mark.asyncio
async def test_closing_sse_cancels_the_runtime_model_and_persists_cancelled() -> None:
    model = BlockingAfterTextModel()
    store = InMemoryVisitConversationStore()
    await store.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id="patient-1",
            patient_display_name="演示患者",
            participant_id="participant-1",
            participant_display_name="患者本人",
            visit_matter_id="visit-1",
            visit_matter_title="取消测试",
        )
    )
    assistant = MediPetAssistant(LangGraphAgentRuntime(model), store)
    stream = to_ui_message_stream(
        assistant.handle_turn(
            TurnCommand(
                visit_matter_id="visit-1",
                participant_id="participant-1",
                idempotency_key="cancel-through-sse",
                message="停止生成",
            )
        )
    )

    for _ in range(4):
        _ = await anext(stream)
    await stream.aclose()

    await asyncio.wait_for(model.released.wait(), timeout=1)
    messages = await store.list_messages("visit-1")
    assert messages[-1].state == "cancelled"
    assert messages[-1].content == "partial"
