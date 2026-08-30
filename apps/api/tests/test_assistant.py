import asyncio
from collections.abc import AsyncIterator

import pytest

from medipet.agent.runtime import AgentRequest, LangGraphAgentRuntime
from medipet.assistant import MediPetAssistant
from medipet.contracts import TurnCommand
from medipet.model.port import (
    ModelChunk,
    ModelMessage,
    ModelPort,
    ModelRequest,
    ModelUnavailableError,
)
from medipet.persistence.conversation import (
    DevelopmentVisitMatter,
    InMemoryVisitConversationStore,
)
from medipet.run_audits import InMemoryRunAuditStore


class DeterministicModel(ModelPort):
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.requests.append(request)
        for text in self._chunks:
            yield ModelChunk(text=text)


async def seeded_store(
    *,
    patient_id: str = "patient-1",
    participant_id: str = "participant-1",
    visit_matter_id: str = "visit-1",
) -> InMemoryVisitConversationStore:
    store = InMemoryVisitConversationStore()
    await store.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id=patient_id,
            patient_display_name="演示患者",
            participant_id=participant_id,
            participant_display_name="患者本人",
            visit_matter_id=visit_matter_id,
            visit_matter_title="初次咨询",
        )
    )
    return store


class UnavailableModel(ModelPort):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        raise ModelUnavailableError("provider secret diagnostic body")
        yield  # pragma: no cover


class NonRetryableModel(ModelPort):
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        raise ModelUnavailableError("invalid request", retryable=False)
        yield  # pragma: no cover


class BlockingModel(ModelPort):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        await asyncio.Event().wait()
        yield ModelChunk(text="unreachable")  # pragma: no cover


class FailOnceModel(ModelPort):
    def __init__(self, *, fail_after_text: bool = False) -> None:
        self.calls = 0
        self.fail_after_text = fail_after_text

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        self.calls += 1
        if self.calls == 1:
            if self.fail_after_text:
                yield ModelChunk(text="半截")
            raise ModelUnavailableError("transient private failure")
        yield ModelChunk(text="恢复成功")


class CountingStore(InMemoryVisitConversationStore):
    def __init__(self) -> None:
        super().__init__()
        self.append_calls = 0

    async def append_assistant_text(self, message_id: str, text: str) -> None:
        self.append_calls += 1
        await super().append_assistant_text(message_id, text)


class HistoryFailureStore(InMemoryVisitConversationStore):
    async def list_completed_messages(
        self,
        visit_matter_id: str,
        *,
        limit: int,
    ):
        del visit_matter_id, limit
        raise RuntimeError("history unavailable")


@pytest.mark.asyncio
async def test_missing_participant_message_has_a_queryable_failed_terminal_audit() -> None:
    audits = InMemoryRunAuditStore()
    assistant = MediPetAssistant(
        LangGraphAgentRuntime(DeterministicModel(["unused"]), audit_store=audits),
        await seeded_store(),
        audit_store=audits,
    )

    events = [
        event
        async for event in assistant.handle_turn(
            TurnCommand(
                visit_matter_id="visit-1",
                participant_id="participant-1",
                idempotency_key="missing-message",
                message=" ",
            )
        )
    ]

    assert [event.kind for event in events] == ["failed"]
    assert [audit.kind for audit in await audits.list_audits()] == ["failed"]


@pytest.mark.asyncio
async def test_runtime_maps_model_failure_to_a_safe_event() -> None:
    runtime = LangGraphAgentRuntime(UnavailableModel())

    events = [
        event
        async for event in runtime.run(
            AgentRequest(messages=(ModelMessage(role="user", content="你好"),))
        )
    ]

    assert [event.kind for event in events] == ["status", "failed"]
    assert events[-1].data == {"message": "模型服务暂时不可用，请稍后重试。"}


@pytest.mark.asyncio
async def test_runtime_retries_once_before_the_first_visible_token() -> None:
    model = FailOnceModel()
    audits = InMemoryRunAuditStore()
    runtime = LangGraphAgentRuntime(model, audit_store=audits)

    events = [
        event
        async for event in runtime.run(
            AgentRequest(
                messages=(ModelMessage(role="user", content="你好"),),
                trace_id="trace-retry",
            )
        )
    ]

    assert model.calls == 2
    assert [event.kind for event in events] == ["status", "text"]
    assert events[-1].data == {"text": "恢复成功"}
    assert [audit.kind for audit in await audits.list_audits()] == ["retry"]


@pytest.mark.asyncio
async def test_runtime_does_not_retry_a_non_transient_failure() -> None:
    model = NonRetryableModel()
    runtime = LangGraphAgentRuntime(model)

    events = [
        event
        async for event in runtime.run(
            AgentRequest(messages=(ModelMessage(role="user", content="你好"),))
        )
    ]

    assert model.calls == 1
    assert [event.kind for event in events] == ["status", "failed"]


@pytest.mark.asyncio
async def test_runtime_does_not_retry_after_a_visible_token() -> None:
    model = FailOnceModel(fail_after_text=True)
    runtime = LangGraphAgentRuntime(model)

    events = [
        event
        async for event in runtime.run(
            AgentRequest(messages=(ModelMessage(role="user", content="你好"),))
        )
    ]

    assert model.calls == 1
    assert [event.kind for event in events] == ["status", "text", "failed"]
    assert [event.data["text"] for event in events if event.kind == "text"] == ["半截"]


@pytest.mark.asyncio
async def test_model_call_timeout_cancels_upstream_and_returns_a_safe_terminal_event() -> None:
    audits = InMemoryRunAuditStore()
    runtime = LangGraphAgentRuntime(
        BlockingModel(),
        model_timeout_seconds=0.01,
        audit_store=audits,
    )

    events = [
        event
        async for event in runtime.run(
            AgentRequest(
                messages=(ModelMessage(role="user", content="你好"),),
                trace_id="trace-model-timeout",
            )
        )
    ]

    assert [event.kind for event in events] == ["status", "failed"]
    assert events[-1].data == {"message": "模型响应已超时，请稍后重试。"}
    assert [audit.kind for audit in await audits.list_audits()] == ["model_timeout"]


@pytest.mark.asyncio
async def test_one_configured_agent_step_allows_one_model_call() -> None:
    runtime = LangGraphAgentRuntime(DeterministicModel(["一步完成"]), max_steps=1)

    events = [
        event
        async for event in runtime.run(
            AgentRequest(messages=(ModelMessage(role="user", content="你好"),))
        )
    ]

    assert [event.kind for event in events] == ["status", "text"]
    assert events[-1].data == {"text": "一步完成"}


@pytest.mark.asyncio
async def test_streams_model_text_with_outpatient_boundaries() -> None:
    model = DeterministicModel(["可以先", "描述主要不适。"])
    store = await seeded_store()
    assistant = MediPetAssistant(LangGraphAgentRuntime(model), store)
    turn = TurnCommand(
        visit_matter_id="visit-1",
        participant_id="participant-1",
        idempotency_key="turn-1",
        message="我这两天头痛",
    )
    events = [event async for event in assistant.handle_turn(turn)]

    assert [event.kind for event in events] == ["status", "text", "text", "completed"]
    assert [event.data["text"] for event in events if event.kind == "text"] == [
        "可以先",
        "描述主要不适。",
    ]
    messages = model.requests[0].messages
    assert messages[-1].role == "user"
    assert messages[-1].content == "我这两天头痛"
    assert messages[0].role == "system"
    assert "不得诊断" in messages[0].content
    assert "不得提供处方" in messages[0].content
    assert "医院数据尚未配置" in messages[0].content
    persisted = await store.list_messages("visit-1")
    assert [(message.role, message.state, message.content) for message in persisted] == [
        ("participant", "completed", "我这两天头痛"),
        ("assistant", "completed", "可以先描述主要不适。"),
    ]


@pytest.mark.asyncio
async def test_maps_model_failure_to_a_safe_terminal_event() -> None:
    store = await seeded_store(
        patient_id="patient-2",
        participant_id="participant-2",
        visit_matter_id="visit-2",
    )
    audits = InMemoryRunAuditStore()
    assistant = MediPetAssistant(
        LangGraphAgentRuntime(UnavailableModel(), audit_store=audits),
        store,
        audit_store=audits,
    )
    turn = TurnCommand(
        visit_matter_id="visit-2",
        participant_id="participant-2",
        idempotency_key="turn-2",
        message="你好",
    )
    events = [event async for event in assistant.handle_turn(turn)]

    assert [event.kind for event in events] == ["status", "failed"]
    assert events[-1].data["message"] == "模型服务暂时不可用，请稍后重试。"
    assert events[-1].data["traceId"].startswith("trace-")
    persisted = await store.list_messages("visit-2")
    assert [message.state for message in persisted] == ["completed", "failed"]
    assert [audit.kind for audit in await audits.list_audits()] == ["retry", "failed"]


@pytest.mark.asyncio
async def test_turn_timeout_stops_the_pinned_runtime_with_a_safe_failure() -> None:
    store = await seeded_store()
    audits = InMemoryRunAuditStore()
    assistant = MediPetAssistant(
        LangGraphAgentRuntime(BlockingModel(), audit_store=audits),
        store,
        turn_timeout_seconds=0.01,
        audit_store=audits,
    )

    events = [
        event
        async for event in assistant.handle_turn(
            TurnCommand(
                visit_matter_id="visit-1",
                participant_id="participant-1",
                idempotency_key="timeout-turn",
                message="超时测试",
            )
        )
    ]

    assert [event.kind for event in events] == ["status", "failed"]
    assert events[-1].data["message"] == "本次协助已超时，请重试。"
    assert "traceId" in events[-1].data
    persisted = await store.list_messages("visit-1")
    assert [message.state for message in persisted] == ["completed", "cancelled"]
    assert [audit.kind for audit in await audits.list_audits()] == [
        "cancelled",
        "turn_timeout",
    ]


@pytest.mark.asyncio
async def test_closing_stream_persists_partial_assistant_text_as_cancelled() -> None:
    store = await seeded_store()
    audits = InMemoryRunAuditStore()
    assistant = MediPetAssistant(
        LangGraphAgentRuntime(DeterministicModel(["半截回答", "不应消费"])),
        store,
        audit_store=audits,
    )
    stream = assistant.handle_turn(
        TurnCommand(
            visit_matter_id="visit-1",
            participant_id="participant-1",
            idempotency_key="cancelled-turn",
            message="停止测试",
        )
    )

    assert (await anext(stream)).kind == "status"
    assert (await anext(stream)).data == {"text": "半截回答"}
    await stream.aclose()

    persisted = await store.list_messages("visit-1")
    assert persisted[-1].state == "cancelled"
    assert persisted[-1].content == "半截回答"
    assert [audit.kind for audit in await audits.list_audits()] == ["cancelled"]


@pytest.mark.asyncio
async def test_assistant_text_is_persisted_in_batches_instead_of_per_chunk() -> None:
    store = CountingStore()
    await store.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id="patient-1",
            patient_display_name="演示患者",
            participant_id="participant-1",
            participant_display_name="患者本人",
            visit_matter_id="visit-1",
            visit_matter_title="初次咨询",
        )
    )
    assistant = MediPetAssistant(
        LangGraphAgentRuntime(DeterministicModel(["字"] * 25)),
        store,
        persistence_batch_characters=10,
    )

    events = [
        event
        async for event in assistant.handle_turn(
            TurnCommand(
                visit_matter_id="visit-1",
                participant_id="participant-1",
                idempotency_key="batched-turn",
                message="批量写入测试",
            )
        )
    ]

    assert events[-1].kind == "completed"
    assert store.append_calls == 3
    persisted = await store.list_messages("visit-1")
    assert persisted[-1].content == "字" * 25


@pytest.mark.asyncio
async def test_next_turn_receives_completed_history_from_the_same_visit() -> None:
    store = await seeded_store()
    model = DeterministicModel(["收到"])
    assistant = MediPetAssistant(LangGraphAgentRuntime(model), store)
    for turn_id, message in (("turn-1", "第一问"), ("turn-2", "第二问")):
        events = [
            event
            async for event in assistant.handle_turn(
                TurnCommand(
                    visit_matter_id="visit-1",
                    participant_id="participant-1",
                    idempotency_key=turn_id,
                    message=message,
                )
            )
        ]
        assert events[-1].kind == "completed"

    second_request = model.requests[1]
    assert [(message.role, message.content) for message in second_request.messages[1:]] == [
        ("user", "第一问"),
        ("assistant", "收到"),
        ("user", "第二问"),
    ]


@pytest.mark.asyncio
async def test_history_load_failure_marks_created_assistant_message_failed() -> None:
    store = HistoryFailureStore()
    await store.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id="patient-1",
            patient_display_name="演示患者",
            participant_id="participant-1",
            participant_display_name="患者本人",
            visit_matter_id="visit-1",
            visit_matter_title="初次咨询",
        )
    )
    assistant = MediPetAssistant(LangGraphAgentRuntime(DeterministicModel(["不会调用"])), store)

    with pytest.raises(RuntimeError, match="history unavailable"):
        _ = [
            event
            async for event in assistant.handle_turn(
                TurnCommand(
                    visit_matter_id="visit-1",
                    participant_id="participant-1",
                    idempotency_key="history-failure-turn",
                    message="历史加载失败测试",
                )
            )
        ]

    persisted = await store.list_messages("visit-1")
    assert [message.state for message in persisted] == ["completed", "failed"]
