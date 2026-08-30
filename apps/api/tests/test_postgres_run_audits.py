from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from medipet.run_audit_postgres import PostgresRunAuditStore

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
async def test_postgres_run_audits_survive_store_recreation() -> None:
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    suffix = uuid4().hex
    turn_id = f"turn-{suffix}"
    first = PostgresRunAuditStore.from_url(DATABASE_URL or "")
    try:
        await first.record(
            "retry",
            trace_id=f"trace-{suffix}",
            visit_matter_id=f"visit-{suffix}",
            turn_id=turn_id,
            profile_version="profile-1",
        )
    finally:
        await first.close()

    restarted = PostgresRunAuditStore.from_url(DATABASE_URL or "")
    try:
        audit = next(item for item in await restarted.list_audits() if item.turn_id == turn_id)
        assert audit.kind == "retry"
        assert audit.profile_version == "profile-1"
    finally:
        await restarted.close()
