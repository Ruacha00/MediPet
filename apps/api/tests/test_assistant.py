from collections.abc import AsyncIterator

import pytest

from medipet.agent.runtime import AgentRequest, LangGraphAgentRuntime
from medipet.assistant import MediPetAssistant
from medipet.contracts import TurnCommand
from medipet.model.port import ModelChunk, ModelPort, ModelRequest, ModelUnavailableError


class DeterministicModel(ModelPort):
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.requests.append(request)
        for text in self._chunks:
            yield ModelChunk(text=text)


class UnavailableModel(ModelPort):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        raise ModelUnavailableError("provider secret diagnostic body")
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_runtime_maps_model_failure_to_a_safe_event() -> None:
    runtime = LangGraphAgentRuntime(UnavailableModel())

    events = [event async for event in runtime.run(AgentRequest(message="你好"))]

    assert [event.kind for event in events] == ["status", "failed"]
    assert events[-1].data == {"message": "模型服务暂时不可用，请稍后重试。"}


@pytest.mark.asyncio
async def test_streams_model_text_with_outpatient_boundaries() -> None:
    model = DeterministicModel(["可以先", "描述主要不适。"])
    assistant = MediPetAssistant(LangGraphAgentRuntime(model))
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


@pytest.mark.asyncio
async def test_maps_model_failure_to_a_safe_terminal_event() -> None:
    assistant = MediPetAssistant(LangGraphAgentRuntime(UnavailableModel()))
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
