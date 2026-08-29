import pytest

from medipet.agent.runtime import LangGraphAgentRuntime
from medipet.assistant import MediPetAssistant
from medipet.contracts import ConfirmationDecision, TurnCommand


@pytest.mark.asyncio
async def test_appointment_requires_confirmation_and_is_idempotent() -> None:
    assistant = MediPetAssistant(LangGraphAgentRuntime())
    turn = TurnCommand(
        visit_matter_id="visit-1",
        participant_id="participant-1",
        idempotency_key="turn-1",
        message="请帮我挂号",
    )

    events = [event async for event in assistant.handle_turn(turn)]
    proposal = next(
        event
        for event in events
        if event.kind == "data" and event.data["type"] == "data-action-proposal"
    )
    assert proposal.data["data"]["status"] == "pending"

    confirmation = TurnCommand(
        visit_matter_id="visit-1",
        participant_id="participant-1",
        idempotency_key="confirmation-1",
        confirmation=ConfirmationDecision(
            proposal_id="proposal-demo-001",
            decision="confirm",
        ),
    )
    first = [event async for event in assistant.handle_turn(confirmation)]
    repeated = [event async for event in assistant.handle_turn(confirmation)]

    first_receipt = next(event for event in first if event.kind == "data")
    repeated_receipt = next(event for event in repeated if event.kind == "data")
    assert first_receipt.data["data"]["receiptId"] == "receipt-demo-001"
    assert repeated_receipt.data["data"]["receiptId"] == "receipt-demo-001"


@pytest.mark.asyncio
async def test_emergency_handoff_precedes_ordinary_guidance() -> None:
    assistant = MediPetAssistant(LangGraphAgentRuntime())
    turn = TurnCommand(
        visit_matter_id="visit-2",
        participant_id="participant-2",
        idempotency_key="turn-2",
        message="患者呼吸困难，想预约普通门诊",
    )

    events = [event async for event in assistant.handle_turn(turn)]
    handoff = next(event for event in events if event.kind == "data")
    assert handoff.data["type"] == "data-handoff"
    assert handoff.data["data"]["priority"] == "emergency"
    assert all("data-action-proposal" not in str(event.data) for event in events)
