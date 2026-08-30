from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from medipet.actions import InMemoryActionStore
from medipet.agent.capabilities import (
    CapabilityProvider,
    CapabilitySnapshot,
    StaticCapabilityProvider,
    ToolContext,
    ToolDefinition,
    VisitStage,
)
from medipet.agent.runtime import LangGraphAgentRuntime
from medipet.assistant import MediPetAssistant
from medipet.contracts import ConfirmationDecision, TurnCommand
from medipet.model.port import ModelChunk, ModelPort, ModelRequest, ModelToolCall
from medipet.persistence.conversation import (
    DevelopmentVisitMatter,
    InMemoryVisitConversationStore,
)
from medipet.run_audits import InMemoryRunAuditStore, RunAuditStore
from medipet.skills.capabilities import RegistryCapabilityProvider
from medipet.skills.registry import InMemorySkillRegistry
from medipet.tools.registry import InMemoryToolRegistry, TrustedTool


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
    action_store: InMemoryActionStore | None = None,
    max_steps: int = 8,
    visit_stage: VisitStage = "pre_visit",
    audit_store: RunAuditStore | None = None,
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
            visit_stage=visit_stage,
        )
    )
    runtime = LangGraphAgentRuntime(
        model,
        capability_provider=(
            capability_provider or StaticCapabilityProvider(snapshot or CapabilitySnapshot.empty())
        ),
        max_steps=max_steps,
        profile_version="profile-1",
        action_store=action_store or InMemoryActionStore(),
        audit_store=audit_store,
    )
    return (
        MediPetAssistant(
            runtime,
            store,
            audit_store=audit_store,
            profile_version="profile-1",
        ),
        store,
    )


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
async def test_write_tool_pauses_with_a_persisted_proposal_before_execution() -> None:
    executions: list[dict[str, object]] = []

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        del context
        executions.append(arguments)
        return {"receipt": "committed"}

    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="测试专用写工具",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"receipt": {"type": "string"}},
            "required": ["receipt"],
            "additionalProperties": False,
        },
        effect="write",
        approval_required=True,
        execute=execute,
    )
    model = ScriptedModel(
        [[ModelChunk(tool_calls=(ModelToolCall("call-write", "dummy_write", {"value": "A"}),))]]
    )
    assistant, _ = await _assistant(
        model,
        CapabilitySnapshot(skill_versions=("test-skill@1",), tools=(tool,)),
    )

    events = [event async for event in assistant.handle_turn(_turn())]

    assert [event.kind for event in events] == ["status", "data", "completed"]
    proposal = events[1].data
    assert proposal["type"] == "data-action-proposal"
    assert proposal["data"]["status"] == "pending"
    assert proposal["data"]["toolId"] == "test.write"
    assert proposal["data"]["toolVersion"] == "1"
    assert proposal["data"]["arguments"] == {"value": "A"}
    assert proposal["data"]["visitMatterId"] == "visit-1"
    assert proposal["data"]["participantId"] == "participant-1"
    assert proposal["data"]["idempotencyKey"]
    assert proposal["data"]["expiresAt"]
    assert executions == []


@pytest.mark.asyncio
async def test_confirming_a_write_proposal_commits_once_and_returns_one_receipt() -> None:
    executions: list[tuple[dict[str, object], ToolContext]] = []

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        executions.append((arguments, context))
        return {"result": "done"}

    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="测试专用写工具",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "additionalProperties": False,
        },
        effect="write",
        approval_required=True,
        execute=execute,
    )
    action_store = InMemoryActionStore()
    audits = InMemoryRunAuditStore()
    assistant, _ = await _assistant(
        ScriptedModel(
            [[ModelChunk(tool_calls=(ModelToolCall("call-write", "dummy_write", {}),))]]
        ),
        CapabilitySnapshot(skill_versions=("test-skill@1",), tools=(tool,)),
        action_store=action_store,
        audit_store=audits,
    )
    proposal_events = [event async for event in assistant.handle_turn(_turn())]
    proposal_id = proposal_events[1].data["data"]["proposalId"]
    confirmation = TurnCommand(
        visit_matter_id="visit-1",
        participant_id="participant-1",
        idempotency_key="decision-1",
        confirmation=ConfirmationDecision(proposal_id=proposal_id, decision="confirm"),
    )

    first = [event async for event in assistant.handle_turn(confirmation)]
    duplicate = [event async for event in assistant.handle_turn(confirmation)]

    assert [event.kind for event in first] == ["data", "completed"]
    assert [event.kind for event in duplicate] == ["data", "completed"]
    assert first[0].data["data"]["status"] == "confirmed"
    assert first[0].data["data"]["receiptId"] == duplicate[0].data["data"]["receiptId"]
    assert len(executions) == 1
    assert executions[0][1].idempotency_key.startswith("action-proposal-")
    terminal_audits = [audit for audit in await audits.list_audits() if audit.kind == "completed"]
    assert [audit.turn_id for audit in terminal_audits] == [
        "turn-1",
        "decision-1",
        "decision-1",
    ]
    assert {audit.profile_version for audit in terminal_audits} == {"profile-1"}


@pytest.mark.asyncio
async def test_rejecting_a_write_proposal_never_executes_it() -> None:
    executions = 0

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        nonlocal executions
        del arguments, context
        executions += 1
        return {"result": "done"}

    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="测试专用写工具",
        input_schema={"type": "object", "additionalProperties": False},
        effect="write",
        approval_required=True,
        execute=execute,
    )
    assistant, _ = await _assistant(
        ScriptedModel(
            [[ModelChunk(tool_calls=(ModelToolCall("call-write", "dummy_write", {}),))]]
        ),
        CapabilitySnapshot(tools=(tool,)),
    )
    proposed = [event async for event in assistant.handle_turn(_turn())]
    proposal_id = proposed[1].data["data"]["proposalId"]

    rejected = [
        event
        async for event in assistant.handle_turn(
            TurnCommand(
                visit_matter_id="visit-1",
                participant_id="participant-1",
                idempotency_key="reject-1",
                confirmation=ConfirmationDecision(proposal_id=proposal_id, decision="reject"),
            )
        )
    ]

    assert [event.kind for event in rejected] == ["data", "completed"]
    assert rejected[0].data["data"]["status"] == "rejected"
    assert executions == 0


@pytest.mark.asyncio
async def test_expired_write_proposal_returns_a_safe_failure_without_execution() -> None:
    now = [datetime.now(UTC)]
    executions = 0

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        nonlocal executions
        del arguments, context
        executions += 1
        return {}

    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="测试专用写工具",
        input_schema={"type": "object", "additionalProperties": False},
        effect="write",
        approval_required=True,
        execute=execute,
    )
    action_store = InMemoryActionStore(now=lambda: now[0])
    assistant, _ = await _assistant(
        ScriptedModel(
            [[ModelChunk(tool_calls=(ModelToolCall("call-write", "dummy_write", {}),))]]
        ),
        CapabilitySnapshot(tools=(tool,)),
        action_store=action_store,
    )
    proposed = [event async for event in assistant.handle_turn(_turn())]
    proposal_id = proposed[1].data["data"]["proposalId"]
    now[0] += timedelta(minutes=11)

    result = [
        event
        async for event in assistant.handle_turn(
            TurnCommand(
                visit_matter_id="visit-1",
                participant_id="participant-1",
                idempotency_key="confirm-expired",
                confirmation=ConfirmationDecision(proposal_id=proposal_id, decision="confirm"),
            )
        )
    ]

    assert [event.kind for event in result] == ["failed"]
    assert result[0].data["message"] == "待确认操作已过期"
    assert executions == 0


@pytest.mark.asyncio
async def test_missing_and_wrong_scope_proposals_return_safe_failures() -> None:
    assistant, store = await _assistant(ScriptedModel([]))
    await store.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id="patient-2",
            patient_display_name="另一患者",
            participant_id="participant-2",
            participant_display_name="患者本人",
            visit_matter_id="visit-2",
            visit_matter_title="另一事项",
        )
    )
    missing = [
        event
        async for event in assistant.handle_turn(
            TurnCommand(
                visit_matter_id="visit-1",
                participant_id="participant-1",
                idempotency_key="missing",
                confirmation=ConfirmationDecision(
                    proposal_id="proposal-missing", decision="confirm"
                ),
            )
        )
    ]
    action_store = InMemoryActionStore()
    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="测试专用写工具",
        input_schema={"type": "object", "additionalProperties": False},
        effect="write",
        approval_required=True,
        execute=lambda arguments, context: _empty_result(arguments, context),
    )
    scoped_assistant, scoped_store = await _assistant(
        ScriptedModel(
            [[ModelChunk(tool_calls=(ModelToolCall("call-write", "dummy_write", {}),))]]
        ),
        CapabilitySnapshot(tools=(tool,)),
        action_store=action_store,
    )
    await scoped_store.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id="patient-2",
            patient_display_name="另一患者",
            participant_id="participant-2",
            participant_display_name="患者本人",
            visit_matter_id="visit-2",
            visit_matter_title="另一事项",
        )
    )
    proposed = [event async for event in scoped_assistant.handle_turn(_turn())]
    proposal_id = proposed[1].data["data"]["proposalId"]
    wrong_scope = [
        event
        async for event in scoped_assistant.handle_turn(
            TurnCommand(
                visit_matter_id="visit-2",
                participant_id="participant-2",
                idempotency_key="wrong-scope",
                confirmation=ConfirmationDecision(proposal_id=proposal_id, decision="confirm"),
            )
        )
    ]

    assert missing[0].data["message"] == "待确认操作不存在"
    assert wrong_scope[0].data["message"] == "该操作不属于当前就诊事项或参与者"


async def _empty_result(
    arguments: dict[str, object], context: ToolContext
) -> dict[str, object]:
    del arguments, context
    return {}


@pytest.mark.asyncio
async def test_tool_version_drift_invalidates_confirmation() -> None:
    executions = 0

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        nonlocal executions
        del arguments, context
        executions += 1
        return {}

    def tool(version: str) -> ToolDefinition:
        return ToolDefinition(
            tool_id="test.write",
            name="dummy_write",
            version=version,
            description="测试专用写工具",
            input_schema={"type": "object", "additionalProperties": False},
            effect="write",
            approval_required=True,
            execute=execute,
        )

    provider = StaticCapabilityProvider(CapabilitySnapshot(tools=(tool("1"),)))
    assistant, _ = await _assistant(
        ScriptedModel(
            [[ModelChunk(tool_calls=(ModelToolCall("call-write", "dummy_write", {}),))]]
        ),
        capability_provider=provider,
    )
    proposed = [event async for event in assistant.handle_turn(_turn())]
    proposal_id = proposed[1].data["data"]["proposalId"]
    provider.replace(CapabilitySnapshot(tools=(tool("2"),)))

    result = [
        event
        async for event in assistant.handle_turn(
            TurnCommand(
                visit_matter_id="visit-1",
                participant_id="participant-1",
                idempotency_key="changed-version",
                confirmation=ConfirmationDecision(proposal_id=proposal_id, decision="confirm"),
            )
        )
    ]

    assert result[0].data["message"] == "操作参数、Tool 版本或作用域已变化，请重新发起"
    assert executions == 0


@pytest.mark.asyncio
async def test_failed_write_execution_is_not_automatically_retried() -> None:
    executions = 0

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        nonlocal executions
        del arguments, context
        executions += 1
        raise RuntimeError("private provider failure")

    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="测试专用写工具",
        input_schema={"type": "object", "additionalProperties": False},
        effect="write",
        approval_required=True,
        execute=execute,
    )
    assistant, _ = await _assistant(
        ScriptedModel(
            [[ModelChunk(tool_calls=(ModelToolCall("call-write", "dummy_write", {}),))]]
        ),
        CapabilitySnapshot(tools=(tool,)),
    )
    proposed = [event async for event in assistant.handle_turn(_turn())]
    proposal_id = proposed[1].data["data"]["proposalId"]

    result = [
        event
        async for event in assistant.handle_turn(
            TurnCommand(
                visit_matter_id="visit-1",
                participant_id="participant-1",
                idempotency_key="failed-write",
                confirmation=ConfirmationDecision(proposal_id=proposal_id, decision="confirm"),
            )
        )
    ]

    assert executions == 1
    assert result[0].data["message"] == "操作暂时无法完成，请稍后重试"
    assert "private provider failure" not in str(result[0].data)


@pytest.mark.asyncio
async def test_model_cannot_execute_a_write_tool_without_platform_approval() -> None:
    executions = 0

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        nonlocal executions
        del arguments, context
        executions += 1
        return {}

    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="未获平台批准的写 Tool",
        input_schema={"type": "object", "additionalProperties": False},
        effect="write",
        approval_required=False,
        execute=execute,
    )
    model = ScriptedModel(
        [
            [ModelChunk(tool_calls=(ModelToolCall("call-write", "dummy_write", {}),))],
            [ModelChunk(text="已改为安全回答")],
        ]
    )
    assistant, _ = await _assistant(model, CapabilitySnapshot(tools=(tool,)))

    events = [event async for event in assistant.handle_turn(_turn())]

    assert events[-1].kind == "completed"
    assert model.requests[0].tools == ()
    assert executions == 0


@pytest.mark.asyncio
async def test_write_tool_fails_safely_when_durable_action_storage_is_not_configured() -> None:
    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="测试专用写 Tool",
        input_schema={"type": "object", "additionalProperties": False},
        effect="write",
        approval_required=True,
        execute=_empty_result,
    )
    model = ScriptedModel(
        [[ModelChunk(tool_calls=(ModelToolCall("call-write", "dummy_write", {}),))]]
    )
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
    assistant = MediPetAssistant(
        LangGraphAgentRuntime(
            model,
            capability_provider=StaticCapabilityProvider(CapabilitySnapshot(tools=(tool,))),
        ),
        store,
    )

    events = [event async for event in assistant.handle_turn(_turn())]

    assert events[-1].kind == "failed"
    assert events[-1].data["message"] == "待确认操作暂时无法创建，请重新发起。"


@pytest.mark.asyncio
async def test_tool_snapshot_is_pinned_for_the_entire_turn() -> None:
    used_versions: list[str] = []

    def versioned_tool(version: str) -> ToolDefinition:
        async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
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
    audits = InMemoryRunAuditStore()
    assistant, store = await _assistant(model, audit_store=audits)

    events = [event async for event in assistant.handle_turn(_turn())]

    assert [event.kind for event in events] == ["status", "failed"]
    assert events[-1].data["message"] == "模型重复请求了不可用操作，本次协助已停止。"
    persisted = await store.list_messages("visit-1")
    assert persisted[-1].state == "failed"
    assert [audit.kind for audit in await audits.list_audits()] == [
        "loop_detected",
        "failed",
    ]


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
            [ModelChunk(tool_calls=(ModelToolCall("call-1", "dummy_read", {"query": 42}),))],
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
    model = ScriptedModel([[ModelChunk(tool_calls=(ModelToolCall("call-1", "dummy_read", {}),))]])
    audits = InMemoryRunAuditStore()
    assistant, store = await _assistant(
        model,
        CapabilitySnapshot(tools=(tool,)),
        max_steps=1,
        audit_store=audits,
    )

    events = [event async for event in assistant.handle_turn(_turn())]

    assert [event.kind for event in events] == ["status", "status", "failed"]
    assert events[-1].data["message"] == "本次协助已达到步骤上限，请重新发起请求。"
    assert len(model.requests) == 1
    persisted = await store.list_messages("visit-1")
    assert persisted[-1].state == "failed"
    assert [audit.kind for audit in await audits.list_audits()] == [
        "budget_exhausted",
        "failed",
    ]


@pytest.mark.asyncio
async def test_tool_action_preamble_is_not_exposed_or_persisted() -> None:
    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        del arguments, context
        return {"value": "observation"}

    tool = ToolDefinition(
        name="dummy_read",
        version="1",
        description="测试安全输出",
        input_schema={"type": "object", "additionalProperties": False},
        effect="read",
        execute=execute,
    )
    model = ScriptedModel(
        [
            [
                ModelChunk(text="Thought: call private tool"),
                ModelChunk(tool_calls=(ModelToolCall("call-1", "dummy_read", {}),)),
            ],
            [ModelChunk(text="参与者可见的最终回答")],
        ]
    )
    assistant, store = await _assistant(model, CapabilitySnapshot(tools=(tool,)))

    events = [event async for event in assistant.handle_turn(_turn())]

    assert [event.data["text"] for event in events if event.kind == "text"] == [
        "参与者可见的最终回答"
    ]
    assert "Thought" not in str([event.data for event in events])
    persisted = await store.list_messages("visit-1")
    assert persisted[-1].content == "参与者可见的最终回答"


class FailingCapabilityProvider:
    async def snapshot(self, context: ToolContext) -> CapabilitySnapshot:
        del context
        raise RuntimeError("private capability diagnostic")


@pytest.mark.asyncio
async def test_capability_snapshot_failure_becomes_a_safe_terminal_event() -> None:
    model = ScriptedModel([[ModelChunk(text="not called")]])
    assistant, store = await _assistant(
        model,
        capability_provider=FailingCapabilityProvider(),
    )

    events = [event async for event in assistant.handle_turn(_turn())]

    assert [event.kind for event in events] == ["failed"]
    assert events[-1].data["message"] == "运行时能力暂时不可用，请稍后重试。"
    assert "private" not in str(events[-1].data)
    assert model.requests == []
    persisted = await store.list_messages("visit-1")
    assert persisted[-1].state == "failed"


@pytest.mark.asyncio
async def test_synced_bound_tool_runs_through_the_complete_react_path_and_is_audited() -> None:
    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        del context
        return {"value": arguments["query"]}

    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await tools.synchronize(
        (
            TrustedTool(
                tool_id="test.lookup",
                version="1",
                name="test_lookup",
                description="Test-only lookup",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                effect="read",
                approval_required=False,
                execute=execute,
                allowed_stages=("in_visit",),
            ),
        ),
        actor="deployment",
    )
    await tools.configure("test.lookup", "1", enabled=True, approval_required=False, actor="admin")
    skill = await skills.create_skill(
        slug="lookup-helper",
        name="Lookup helper",
        description="Uses lookup",
        instructions="Use the lookup Tool.",
        change_note="Initial",
        skill_type="tool-assisted",
        actor="admin",
    )
    await tools.bind(skill.skill_id, 1, "test.lookup", "1", actor="admin")
    await skills.transition(skill.skill_id, 1, "submit_review", actor="admin")
    await skills.transition(skill.skill_id, 1, "publish", actor="admin")
    model = ScriptedModel(
        [
            [ModelChunk(tool_calls=(ModelToolCall("call-1", "test_lookup", {"query": "result"}),))],
            [ModelChunk(text="Done")],
        ]
    )
    assistant, _ = await _assistant(
        model,
        capability_provider=RegistryCapabilityProvider(skills, tools),
        visit_stage="in_visit",
    )

    events = [event async for event in assistant.handle_turn(_turn())]
    audits = await tools.list_audits()

    assert events[-1].kind == "completed"
    assert json.loads(model.requests[1].messages[-1].content) == {"value": "result"}
    invocation = next(audit for audit in audits if audit.action == "invoke")
    assert invocation.visit_matter_id == "visit-1"
    assert invocation.turn_id == "turn-1"
