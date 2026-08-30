from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from test_conversation_store import exercise_store_contract

from medipet.persistence.conversation import DevelopmentVisit
from medipet.persistence.postgres import PostgresVisitConversationStore

DATABASE_URL = os.getenv("MEDIPET_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="MEDIPET_TEST_DATABASE_URL is required for PostgreSQL contract tests",
)


def alembic_config() -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", DATABASE_URL or "")
    return config


async def upgrade_database() -> None:
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")


@pytest.mark.asyncio
async def test_migrations_and_postgres_store_contract_are_repeatable() -> None:
    await upgrade_database()
    await upgrade_database()

    store = PostgresVisitConversationStore.from_url(DATABASE_URL or "")
    suffix = uuid4().hex

    def seed() -> DevelopmentVisit:
        return DevelopmentVisit(
            patient_id=f"patient-{suffix}",
            patient_display_name="演示患者",
            participant_id=f"participant-{suffix}",
            participant_display_name="患者本人",
            visit_matter_id=f"visit-{suffix}",
            visit_matter_title="初次咨询",
        )

    try:
        await exercise_store_contract(store, seed)
        await store.seed_development_visit(seed())
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_interleaved_turn_completion_preserves_each_message() -> None:
    await upgrade_database()
    store = PostgresVisitConversationStore.from_url(DATABASE_URL or "")
    suffix = uuid4().hex
    visit = DevelopmentVisit(
        patient_id=f"patient-{suffix}",
        patient_display_name="演示患者",
        participant_id=f"participant-{suffix}",
        participant_display_name="患者本人",
        visit_matter_id=f"visit-{suffix}",
        visit_matter_title="并发咨询",
    )
    try:
        await store.seed_development_visit(visit)
        older = await store.add_assistant_message(
            visit_matter_id=visit.visit_matter_id,
            participant_id=visit.participant_id,
            turn_id="older-turn",
        )
        newer = await store.add_assistant_message(
            visit_matter_id=visit.visit_matter_id,
            participant_id=visit.participant_id,
            turn_id="newer-turn",
        )
        for message, text in ((newer, "较新回答"), (older, "较早回答")):
            await store.mark_assistant_streaming(message.id)
            await store.append_assistant_text(message.id, text)
            await store.finish_assistant_message(message.id, "completed")

        messages = await store.list_messages(visit.visit_matter_id)
        assert [(message.turn_id, message.content) for message in messages] == [
            ("older-turn", "较早回答"),
            ("newer-turn", "较新回答"),
        ]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_completed_history_survives_store_recreation() -> None:
    await upgrade_database()
    suffix = uuid4().hex
    visit = DevelopmentVisit(
        patient_id=f"patient-{suffix}",
        patient_display_name="演示患者",
        participant_id=f"participant-{suffix}",
        participant_display_name="患者本人",
        visit_matter_id=f"visit-{suffix}",
        visit_matter_title="重启恢复",
    )
    first_store = PostgresVisitConversationStore.from_url(DATABASE_URL or "")
    await first_store.seed_development_visit(visit)
    await first_store.add_participant_message(
        visit_matter_id=visit.visit_matter_id,
        participant_id=visit.participant_id,
        turn_id="restart-turn",
        content="重启前消息",
    )
    assistant = await first_store.add_assistant_message(
        visit_matter_id=visit.visit_matter_id,
        participant_id=visit.participant_id,
        turn_id="restart-turn",
    )
    await first_store.mark_assistant_streaming(assistant.id)
    await first_store.append_assistant_text(assistant.id, "重启后仍可见")
    await first_store.finish_assistant_message(assistant.id, "completed")
    await first_store.close()

    restarted_store = PostgresVisitConversationStore.from_url(DATABASE_URL or "")
    try:
        messages = await restarted_store.list_messages(visit.visit_matter_id)
        assert [(message.role, message.content) for message in messages] == [
            ("user", "重启前消息"),
            ("assistant", "重启后仍可见"),
        ]
    finally:
        await restarted_store.close()
