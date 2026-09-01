from __future__ import annotations

from collections.abc import Callable

import pytest

from medipet.persistence.conversation import (
    DevelopmentVisitMatter,
    IdempotencyConflictError,
    InMemoryVisitConversationStore,
    MessageTransitionError,
    VisitContext,
    VisitConversationStore,
    VisitMatterArchivedError,
    VisitMatterBusyError,
    VisitTurn,
)


async def exercise_store_contract(
    store: VisitConversationStore,
    seed: Callable[[], DevelopmentVisitMatter],
) -> None:
    visit = seed()
    await store.seed_development_visit_matter(visit)
    assert await store.visit_context(
        visit.visit_matter_id,
        visit.participant_id,
    ) == VisitContext(
        patient_id=visit.patient_id,
        patient_display_name=visit.patient_display_name,
        visit_stage=visit.visit_stage,
    )
    created_visit = await store.create_visit_matter(
        participant_id=visit.participant_id,
        title="复诊准备",
    )
    assert created_visit.visit_matter_id != visit.visit_matter_id
    assert created_visit.patient_display_name == visit.patient_display_name
    assert created_visit.participant_display_name == visit.participant_display_name
    assert [
        item.visit_matter_id for item in await store.list_visit_matters(visit.participant_id)
    ] == [created_visit.visit_matter_id, visit.visit_matter_id]
    renamed_visit = await store.rename_visit_matter(
        visit.visit_matter_id,
        visit.participant_id,
        title="复诊问题整理",
    )
    assert renamed_visit.title == "复诊问题整理"
    assert [
        item.visit_matter_id for item in await store.list_visit_matters(visit.participant_id)
    ] == [created_visit.visit_matter_id, visit.visit_matter_id]
    archived_visit = await store.archive_visit_matter(
        created_visit.visit_matter_id,
        visit.participant_id,
    )
    assert archived_visit.archived_at is not None
    assert [
        item.visit_matter_id for item in await store.list_visit_matters(visit.participant_id)
    ] == [visit.visit_matter_id]
    assert [
        item.visit_matter_id
        for item in await store.list_visit_matters(visit.participant_id, archived=True)
    ] == [created_visit.visit_matter_id]
    renamed_archived_visit = await store.rename_visit_matter(
        created_visit.visit_matter_id,
        visit.participant_id,
        title="已归档复诊准备",
    )
    assert renamed_archived_visit.title == "已归档复诊准备"
    restored_visit = await store.restore_visit_matter(
        created_visit.visit_matter_id,
        visit.participant_id,
    )
    assert restored_visit.archived_at is None
    assert [
        item.visit_matter_id for item in await store.list_visit_matters(visit.participant_id)
    ] == [created_visit.visit_matter_id, visit.visit_matter_id]
    assert await store.list_messages(created_visit.visit_matter_id) == []
    turn = VisitTurn(
        visit_matter_id=visit.visit_matter_id,
        participant_id=visit.participant_id,
        turn_id="turn-1",
    )
    participant = await store.add_participant_message(
        turn=turn,
        content="我这两天头痛",
        selected_slot_id="slot-1",
    )
    replayed_participant = await store.add_participant_message(
        turn=turn,
        content="我这两天头痛",
        selected_slot_id="slot-1",
    )
    with pytest.raises(IdempotencyConflictError):
        await store.add_participant_message(
            turn=turn,
            content="我这两天头痛",
            selected_slot_id="slot-2",
        )
    with pytest.raises(VisitMatterBusyError):
        await store.archive_visit_matter(visit.visit_matter_id, visit.participant_id)
    assistant, claimed = await store.claim_assistant_message(turn=turn)
    replayed_assistant, replayed_claim = await store.claim_assistant_message(turn=turn)
    assert participant.state == "completed"
    assert participant.selected_slot_id == "slot-1"
    assert replayed_participant.id == participant.id
    assert assistant.state == "pending"
    assert claimed is True
    assert replayed_claim is False
    assert replayed_assistant.id == assistant.id
    with pytest.raises(VisitMatterBusyError):
        await store.archive_visit_matter(visit.visit_matter_id, visit.participant_id)

    proposal_part = {
        "type": "data-action-proposal",
        "data": {"proposalId": "proposal-1", "status": "pending"},
    }
    assert not await store.has_action_proposal_part(visit.visit_matter_id, "proposal-1")
    await store.append_assistant_part(assistant.id, proposal_part)
    assert await store.has_action_proposal_part(visit.visit_matter_id, "proposal-1")
    await store.mark_assistant_streaming(assistant.id)
    await store.append_assistant_text(assistant.id, "可以先")
    await store.append_assistant_text(assistant.id, "记录持续时间。")
    await store.finish_assistant_message(assistant.id, "completed")
    with pytest.raises(VisitMatterBusyError):
        await store.archive_visit_matter(visit.visit_matter_id, visit.participant_id)

    messages = await store.list_messages(visit.visit_matter_id)
    assert [(message.role, message.state, message.content) for message in messages] == [
        ("participant", "completed", "我这两天头痛"),
        ("assistant", "completed", "可以先记录持续时间。"),
    ]
    assert messages[-1].parts == (proposal_part,)
    confirmed_part = {
        "type": "data-action-proposal",
        "data": {
            "proposalId": "proposal-1",
            "status": "confirmed",
            "receiptId": "receipt-1",
        },
    }
    await store.update_action_proposal_part(
        visit.visit_matter_id,
        "proposal-1",
        confirmed_part,
    )
    updated = await store.list_messages(visit.visit_matter_id)
    assert updated[-1].parts == (confirmed_part,)
    with pytest.raises(MessageTransitionError):
        await store.append_assistant_text(assistant.id, "不应再写入")
    archived = await store.archive_visit_matter(
        visit.visit_matter_id,
        visit.participant_id,
    )
    assert archived.archived_at is not None
    assert await store.list_messages(visit.visit_matter_id) == updated
    with pytest.raises(VisitMatterArchivedError):
        await store.visit_context(visit.visit_matter_id, visit.participant_id)
    with pytest.raises(VisitMatterArchivedError):
        await store.add_participant_message(
            turn=VisitTurn(
                visit_matter_id=visit.visit_matter_id,
                participant_id=visit.participant_id,
                turn_id="turn-after-archive",
            ),
            content="继续咨询",
        )
    await store.restore_visit_matter(visit.visit_matter_id, visit.participant_id)
    await store.rename_visit_matter(
        visit.visit_matter_id,
        visit.participant_id,
        title=visit.visit_matter_title,
    )
    assert (
        await store.visit_context(visit.visit_matter_id, visit.participant_id)
    ).patient_id == visit.patient_id


@pytest.mark.asyncio
async def test_in_memory_store_satisfies_conversation_contract() -> None:
    store = InMemoryVisitConversationStore()

    def seed() -> DevelopmentVisitMatter:
        return DevelopmentVisitMatter(
            patient_id="patient-1",
            patient_display_name="演示患者",
            participant_id="participant-1",
            participant_display_name="患者本人",
            visit_matter_id="visit-1",
            visit_matter_title="初次咨询",
        )

    await exercise_store_contract(store, seed)


@pytest.mark.asyncio
async def test_completed_context_is_isolated_by_visit_and_limited() -> None:
    store = InMemoryVisitConversationStore()
    visits = [
        DevelopmentVisitMatter(
            patient_id=f"patient-{index}",
            patient_display_name=f"演示患者 {index}",
            participant_id=f"participant-{index}",
            participant_display_name="患者本人",
            visit_matter_id=f"visit-{index}",
            visit_matter_title="初次咨询",
        )
        for index in (1, 2)
    ]
    for visit in visits:
        await store.seed_development_visit_matter(visit)

    for index in range(12):
        turn = VisitTurn(
            visit_matter_id="visit-1",
            participant_id="participant-1",
            turn_id=f"turn-{index}",
        )
        await store.add_participant_message(
            turn=turn,
            content=f"问题 {index}",
        )
        assistant = await store.add_assistant_message(turn=turn)
        await store.finish_assistant_message(assistant.id, "failed")

    other_turn = VisitTurn(
        visit_matter_id="visit-2",
        participant_id="participant-2",
        turn_id="other-turn",
    )
    await store.add_participant_message(
        turn=other_turn,
        content="另一个事项的内容",
    )

    context = await store.list_completed_messages("visit-1", limit=10)

    assert len(context) == 10
    assert context[0].content == "问题 2"
    assert context[-1].content == "问题 11"
    assert all(message.role == "participant" for message in context)
    assert all("另一个事项" not in message.content for message in context)
