from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from medipet.agent.capabilities import (
    CapabilityProvider,
    CapabilitySnapshot,
    StaticCapabilityProvider,
    ToolContext,
    ToolDefinition,
)
from medipet.agent.runtime import LangGraphAgentRuntime
from medipet.assistant import MediPetAssistant
from medipet.contracts import TurnCommand
from medipet.model.port import ModelChunk, ModelPort, ModelRequest, ModelToolCall
from medipet.persistence.conversation import (
    DevelopmentVisitMatter,
    InMemoryVisitConversationStore,
)


class ScriptedModel(ModelPort):
    def __init__(self, responses: list[list[ModelChunk]]) -> None:
        self._responses = responses
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        self.requests.append(request)
        for chunk in self._responses[len(self.requests) - 1]:
            yield chunk


async def _assistant(
    model: ModelPort,
    snapshot: CapabilitySnapshot | None = None,
    *,
    capability_provider: CapabilityProvider | None = None,
    max_steps: int = 8,
) -> tuple[MediPetAssistant, InMemoryVisitConversationStore]:
    store = InMemoryVisitConversationStore()
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
    runtime = LangGraphAgentRuntime(
        model,
        capability_provider=(
            capability_provider
            or StaticCapabilityProvider(snapshot or CapabilitySnapshot.empty())
        ),
        max_steps=max_steps,
        profile_version="profile-1",
    )
    return MediPetAssistant(runtime, store), store


def _turn(turn_id: str = "turn-1") -> TurnCommand:
    return TurnCommand(
        visit_matter_id="visit-1",
        participant_id="participant-1",
        idempotency_key=turn_id,
        message="请查询测试信息",
    )


@pytest.mark.asyncio
async def test_zero_capability_runtime_streams_text_and_completes() -> None:
    model = ScriptedModel([[ModelChunk(text="正常回复")]])
    assistant, _ = await _assistant(model)

    events = [event async for event in assistant.handle_turn(_turn())]

    assert [event.kind for event in events] == ["status", "text", "completed"]
    assert model.requests[0].tools == ()


@pytest.mark.asyncio
async def test_test_only_read_tool_returns_observation_to_the_model() -> None:
    executions: list[tuple[dict[str, object], ToolContext]] = []

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        executions.append((arguments, context))
        return {"value": "观测结果", "query": arguments["query"]}

    tool = ToolDefinition(
        name="dummy_read",
        version="1",
        description="测试专用只读工具",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        effect="read",
        execute=execute,
    )
    model = ScriptedModel(
        [
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall(
                            id="call-1",
                            name="dummy_read",
                            arguments={"query": "示例"},
                        ),
                    )
                )
            ],
            [ModelChunk(text="已根据查询结果回答")],
        ]
    )
    assistant, _ = await _assistant(
        model,
        CapabilitySnapshot(skill_versions=("test-skill@1",), tools=(tool,)),
    )

    events = [event async for event in assistant.handle_turn(_turn())]

    assert [event.kind for event in events] == ["status", "status", "text", "completed"]
    assert executions[0][0] == {"query": "示例"}
    assert executions[0][1].visit_matter_id == "visit-1"
    assert executions[0][1].profile_version == "profile-1"
    assert model.requests[0].tools[0].name == "dummy_read"
    observation = model.requests[1].messages[-1]
    assert observation.role == "tool"
    assert observation.tool_call_id == "call-1"
    assert json.loads(observation.content) == {"query": "示例", "value": "观测结果"}


@pytest.mark.asyncio
async def test_tool_snapshot_is_pinned_for_the_entire_turn() -> None:
    used_versions: list[str] = []

    def versioned_tool(version: str) -> ToolDefinition:
        async def execute(
            arguments: dict[str, object], context: ToolContext
        ) -> dict[str, object]:
            del arguments, context
            used_versions.append(version)
            return {"version": version}

        return ToolDefinition(
            name="dummy_read",
            version=version,
            description="测试版本固定",
            input_schema={"type": "object", "additionalProperties": False},
            effect="read",
            execute=execute,
        )

    provider = StaticCapabilityProvider(
        CapabilitySnapshot(skill_versions=("test-skill@1",), tools=(versioned_tool("1"),))
    )
    model = ScriptedModel(
        [
            [ModelChunk(tool_calls=(ModelToolCall("call-1", "dummy_read", {}),))],
            [ModelChunk(text="完成")],
        ]
    )
    assistant, _ = await _assistant(model, capability_provider=provider)
    stream = assistant.handle_turn(_turn())

    first = await anext(stream)
    provider.replace(
        CapabilitySnapshot(skill_versions=("test-skill@2",), tools=(versioned_tool("2"),))
    )
    remaining = [event async for event in stream]

    assert first.kind == "status"
    assert remaining[-1].kind == "completed"
    assert used_versions == ["1"]


@pytest.mark.parametrize(
    ("tool_name", "enabled", "bound", "authorized"),
    [
        ("unknown", True, True, True),
        ("dummy_read", False, True, True),
        ("dummy_read", True, False, True),
        ("dummy_read", True, True, False),
    ],
)
@pytest.mark.asyncio
async def test_invalid_or_unavailable_tool_gets_one_safe_correction_without_execution(
    tool_name: str,
    enabled: bool,
    bound: bool,
    authorized: bool,
) -> None:
    executions = 0

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        nonlocal executions
        del arguments, context
        executions += 1
        return {"should": "not execute"}

    tool = ToolDefinition(
        name="dummy_read",
        version="1",
        description="测试拒绝",
        input_schema={"type": "object", "additionalProperties": False},
        effect="read",
        execute=execute,
        enabled=enabled,
        bound=bound,
        authorize=lambda context: authorized,
    )
    invalid = ModelToolCall("call-1", tool_name, {})
    model = ScriptedModel(
        [[ModelChunk(tool_calls=(invalid,))], [ModelChunk(text="已改为安全回答")]]
    )
    assistant, _ = await _assistant(model, CapabilitySnapshot(tools=(tool,)))

    events = [event async for event in assistant.handle_turn(_turn())]

    assert events[-1].kind == "completed"
    assert executions == 0
    correction = model.requests[1].messages[-1]
    assert correction.role == "tool"
    assert json.loads(correction.content) == {
        "code": "tool_call_rejected",
        "message": "该操作不可用，请改为安全回答。",
    }
    assert "dummy_read" not in str([event.data for event in events])


@pytest.mark.asyncio
async def test_repeated_invalid_tool_call_terminates_the_loop() -> None:
    invalid = ModelToolCall("call-1", "unknown", {})
    repeated = ModelToolCall("call-2", "unknown", {})
    model = ScriptedModel(
        [
            [ModelChunk(tool_calls=(invalid,))],
            [ModelChunk(tool_calls=(repeated,))],
        ]
    )
    assistant, store = await _assistant(model)

    events = [event async for event in assistant.handle_turn(_turn())]

    assert [event.kind for event in events] == ["status", "failed"]
    assert events[-1].data["message"] == "模型重复请求了不可用操作，本次协助已停止。"
    persisted = await store.list_messages("visit-1")
    assert persisted[-1].state == "failed"


@pytest.mark.asyncio
async def test_invalid_tool_arguments_are_rejected_before_execution() -> None:
    executions = 0

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        nonlocal executions
        del arguments, context
        executions += 1
        return {}

    tool = ToolDefinition(
        name="dummy_read",
        version="1",
        description="测试参数验证",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        effect="read",
        execute=execute,
    )
    model = ScriptedModel(
        [
            [
                ModelChunk(
                    tool_calls=(
                        ModelToolCall("call-1", "dummy_read", {"query": 42}),
                    )
                )
            ],
            [ModelChunk(text="安全回答")],
        ]
    )
    assistant, _ = await _assistant(model, CapabilitySnapshot(tools=(tool,)))

    events = [event async for event in assistant.handle_turn(_turn())]

    assert events[-1].kind == "completed"
    assert executions == 0


@pytest.mark.asyncio
async def test_model_call_budget_terminates_a_tool_loop() -> None:
    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        del arguments, context
        return {"value": "observation"}

    tool = ToolDefinition(
        name="dummy_read",
        version="1",
        description="测试步骤预算",
        input_schema={"type": "object", "additionalProperties": False},
        effect="read",
        execute=execute,
    )
    model = ScriptedModel(
        [[ModelChunk(tool_calls=(ModelToolCall("call-1", "dummy_read", {}),))]]
    )
    assistant, store = await _assistant(
        model,
        CapabilitySnapshot(tools=(tool,)),
        max_steps=1,
    )

    events = [event async for event in assistant.handle_turn(_turn())]

    assert [event.kind for event in events] == ["status", "status", "failed"]
    assert events[-1].data["message"] == "本次协助已达到步骤上限，请重新发起请求。"
    assert len(model.requests) == 1
    persisted = await store.list_messages("visit-1")
    assert persisted[-1].state == "failed"
