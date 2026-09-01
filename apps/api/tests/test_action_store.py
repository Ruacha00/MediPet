from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from medipet.actions import ActionDecisionError, InMemoryActionStore
from medipet.agent.capabilities import (
    ToolConfirmationContract,
    ToolContext,
    ToolDefinition,
)


async def exercise_action_store_contract(store, *, suffix: str = "memory") -> None:
    executions = 0

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        nonlocal executions
        executions += 1
        return {
            "value": arguments["value"],
            "actionKey": context.idempotency_key,
            "patientId": context.patient_id,
        }

    async def revalidate_confirmation(
        arguments: dict[str, object],
        confirmation: dict[str, object],
        context: ToolContext,
    ) -> bool:
        return confirmation == {
            "patient_id": context.patient_id,
            "value": arguments["value"],
        }

    async def prepare_confirmation(
        arguments: dict[str, object], context: ToolContext
    ) -> dict[str, object]:
        return {
            "patient_id": context.patient_id,
            "value": arguments["value"],
        }

    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="测试专用写 Tool",
        input_schema={"type": "object"},
        confirmation_contract=ToolConfirmationContract(
            schema={
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["patient_id", "value"],
                "additionalProperties": False,
            },
            prepare=prepare_confirmation,
            revalidate=revalidate_confirmation,
        ),
        effect="write",
        approval_required=True,
        execute=execute,
    )
    turn_context = ToolContext(
        visit_matter_id=f"visit-{suffix}",
        participant_id=f"participant-{suffix}",
        idempotency_key="turn-write",
        patient_id=f"patient-{suffix}",
    )
    proposal = await store.create_proposal(
        tool,
        {"value": "A"},
        turn_context,
        confirmation={
            "patient_id": turn_context.patient_id,
            "value": "A",
        },
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    assert proposal.patient_id == turn_context.patient_id
    duplicate_proposal = await store.create_proposal(
        tool,
        {"value": "A"},
        turn_context,
        confirmation={
            "patient_id": turn_context.patient_id,
            "value": "changed-after-first-proposal",
        },
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    assert duplicate_proposal == proposal
    assert proposal.confirmation == {
        "patient_id": turn_context.patient_id,
        "value": "A",
    }
    assert await store.has_pending_proposal(
        turn_context.visit_matter_id,
        turn_context.participant_id,
    )
    assert not await store.has_pending_proposal(
        turn_context.visit_matter_id,
        "another-participant",
    )
    decision_context = ToolContext(
        visit_matter_id=turn_context.visit_matter_id,
        participant_id=turn_context.participant_id,
        idempotency_key="decision-confirm",
        patient_id=turn_context.patient_id,
    )

    confirmed, receipt = await store.confirm(
        proposal.proposal_id, decision_context, tool
    )
    duplicate, duplicate_receipt = await store.confirm(
        proposal.proposal_id, decision_context, tool
    )

    assert confirmed.status == duplicate.status == "confirmed"
    assert not await store.has_pending_proposal(
        turn_context.visit_matter_id,
        turn_context.participant_id,
    )
    assert receipt == duplicate_receipt
    assert receipt.result["actionKey"] == proposal.idempotency_key
    assert receipt.result["patientId"] == turn_context.patient_id
    assert executions == 1
    assert [
        item.action
        for item in await store.list_audits()
        if item.proposal_id == proposal.proposal_id
    ] == [
        "propose",
        "confirm",
        "confirm",
    ]
    assert [
        item for item in await store.list_receipts() if item.proposal_id == proposal.proposal_id
    ] == [receipt]


@pytest.mark.asyncio
async def test_in_memory_action_store_contract() -> None:
    await exercise_action_store_contract(InMemoryActionStore())


@pytest.mark.asyncio
async def test_write_proposal_requires_authoritative_patient_scope() -> None:
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

    with pytest.raises(ActionDecisionError, match="患者作用域"):
        await InMemoryActionStore().create_proposal(
            tool,
            {},
            ToolContext(
                visit_matter_id="visit-1",
                participant_id="participant-1",
                idempotency_key="turn-1",
            ),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )


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
    context = ToolContext(
        "visit-1", "participant-1", "turn-1", patient_id="patient-1"
    )
    proposal = await store.create_proposal(
        tool,
        {},
        context,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    with pytest.raises(ActionDecisionError, match="操作暂时无法完成"):
        await store.confirm(
            proposal.proposal_id,
            ToolContext(
                "visit-1", "participant-1", "decision-1", patient_id="patient-1"
            ),
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
    context = ToolContext(
        "visit-1", "participant-1", "turn-1", patient_id="patient-1"
    )
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    await store.create_proposal(tool, {"value": "A"}, context, expires_at=expires_at)

    with pytest.raises(ActionDecisionError, match="不能改变操作参数"):
        await store.create_proposal(tool, {"value": "B"}, context, expires_at=expires_at)


@pytest.mark.asyncio
async def test_visit_stage_and_profile_changes_invalidate_confirmation() -> None:
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
    proposal = await store.create_proposal(
        tool,
        {},
            ToolContext(
                "visit-1",
                "participant-1",
                "turn-1",
                profile_version="profile-1",
                visit_stage="pre_visit",
                patient_id="patient-1",
            ),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    with pytest.raises(ActionDecisionError, match="作用域已变化"):
        await store.confirm(
            proposal.proposal_id,
            ToolContext(
                "visit-1",
                "participant-1",
                "decision-1",
                profile_version="profile-2",
                visit_stage="in_visit",
                patient_id="patient-1",
            ),
            tool,
        )


@pytest.mark.asyncio
async def test_patient_change_invalidates_confirmation() -> None:
    tool = ToolDefinition(
        tool_id="test.write",
        name="dummy_write",
        version="1",
        description="测试患者作用域",
        input_schema={"type": "object"},
        effect="write",
        approval_required=True,
        execute=lambda arguments, context: _unused_execute(arguments, context),
    )
    store = InMemoryActionStore()
    proposal = await store.create_proposal(
        tool,
        {},
        ToolContext(
            "visit-1",
            "participant-1",
            "turn-1",
            patient_id="patient-1",
        ),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    with pytest.raises(ActionDecisionError, match="作用域已变化"):
        await store.confirm(
            proposal.proposal_id,
            ToolContext(
                "visit-1",
                "participant-1",
                "decision-1",
                patient_id="patient-2",
            ),
            tool,
        )


async def _unused_execute(
    arguments: dict[str, object], context: ToolContext
) -> dict[str, object]:
    del arguments, context
    return {}
