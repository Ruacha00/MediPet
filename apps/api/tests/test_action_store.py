from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from medipet.actions import ActionDecisionError, InMemoryActionStore
from medipet.agent.capabilities import ToolContext, ToolDefinition


async def exercise_action_store_contract(store, *, suffix: str = "memory") -> None:
    executions = 0

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        nonlocal executions
        executions += 1
        return {"value": arguments["value"], "actionKey": context.idempotency_key}

    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="测试专用写 Tool",
        input_schema={"type": "object"},
        effect="write",
        approval_required=True,
        execute=execute,
    )
    turn_context = ToolContext(
        visit_matter_id=f"visit-{suffix}",
        participant_id=f"participant-{suffix}",
        idempotency_key="turn-write",
    )
    proposal = await store.create_proposal(
        tool,
        {"value": "A"},
        turn_context,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    decision_context = ToolContext(
        visit_matter_id=turn_context.visit_matter_id,
        participant_id=turn_context.participant_id,
        idempotency_key="decision-confirm",
    )

    confirmed, receipt = await store.confirm(
        proposal.proposal_id, decision_context, tool
    )
    duplicate, duplicate_receipt = await store.confirm(
        proposal.proposal_id, decision_context, tool
    )

    assert confirmed.status == duplicate.status == "confirmed"
    assert receipt == duplicate_receipt
    assert receipt.result["actionKey"] == proposal.idempotency_key
    assert executions == 1
    assert [item.action for item in await store.list_audits()] == ["propose", "confirm"]
    assert await store.list_receipts() == [receipt]


@pytest.mark.asyncio
async def test_in_memory_action_store_contract() -> None:
    await exercise_action_store_contract(InMemoryActionStore())


@pytest.mark.asyncio
async def test_invalid_write_tool_output_never_creates_a_receipt() -> None:
    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        del arguments, context
        return {"saved": 42}

    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="测试专用写 Tool",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={
            "type": "object",
            "properties": {"saved": {"type": "string"}},
            "required": ["saved"],
            "additionalProperties": False,
        },
        effect="write",
        approval_required=True,
        execute=execute,
    )
    store = InMemoryActionStore()
    context = ToolContext("visit-1", "participant-1", "turn-1")
    proposal = await store.create_proposal(
        tool,
        {},
        context,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    with pytest.raises(ActionDecisionError, match="操作暂时无法完成"):
        await store.confirm(
            proposal.proposal_id,
            ToolContext("visit-1", "participant-1", "decision-1"),
            tool,
        )

    assert await store.list_receipts() == []


@pytest.mark.asyncio
async def test_same_request_cannot_change_write_parameters() -> None:
    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="测试专用写 Tool",
        input_schema={"type": "object"},
        effect="write",
        approval_required=True,
        execute=lambda arguments, context: _unused_execute(arguments, context),
    )
    store = InMemoryActionStore()
    context = ToolContext("visit-1", "participant-1", "turn-1")
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    await store.create_proposal(tool, {"value": "A"}, context, expires_at=expires_at)

    with pytest.raises(ActionDecisionError, match="不能改变操作参数"):
        await store.create_proposal(tool, {"value": "B"}, context, expires_at=expires_at)


async def _unused_execute(
    arguments: dict[str, object], context: ToolContext
) -> dict[str, object]:
    del arguments, context
    return {}
