from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from test_action_store import exercise_action_store_contract

from medipet.action_postgres import PostgresActionStore
from medipet.persistence.conversation import DevelopmentVisitMatter
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


@pytest.mark.asyncio
async def test_postgres_action_store_survives_recreation_and_is_idempotent() -> None:
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    suffix = uuid4().hex
    conversations = PostgresVisitConversationStore.from_url(DATABASE_URL or "")
    await conversations.seed_development_visit_matter(
        DevelopmentVisitMatter(
            patient_id=f"patient-{suffix}",
            patient_display_name="演示患者",
            participant_id=f"participant-{suffix}",
            participant_display_name="患者本人",
            visit_matter_id=f"visit-{suffix}",
            visit_matter_title="写操作测试",
        )
    )
    first = PostgresActionStore.from_url(DATABASE_URL or "")
    try:
        await exercise_action_store_contract(first, suffix=suffix)
        proposals = await first.list_proposals()
        proposal_id = proposals[-1].proposal_id
    finally:
        await first.close()
        await conversations.close()

    restarted = PostgresActionStore.from_url(DATABASE_URL or "")
    try:
        assert (await restarted.get_proposal(proposal_id)).status == "confirmed"
        assert (await restarted.list_receipts())[-1].proposal_id == proposal_id
    finally:
        await restarted.close()
