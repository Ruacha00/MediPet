from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from medipet.agent.capabilities import ToolContext
from medipet.skills.postgres import PostgresSkillRegistry

DATABASE_URL = os.getenv("MEDIPET_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="MEDIPET_TEST_DATABASE_URL is required for PostgreSQL contract tests",
)


async def _upgrade_database() -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", DATABASE_URL or "")
    await asyncio.to_thread(command.upgrade, config, "head")


@pytest.mark.asyncio
async def test_postgres_skill_registry_persists_versions_and_runtime_audits() -> None:
    await _upgrade_database()
    registry = PostgresSkillRegistry.from_url(DATABASE_URL or "")
    slug = f"visit-preparation-{uuid4().hex}"
    try:
        draft = await registry.create_skill(
            slug=slug,
            name="就诊准备",
            description="准备说明",
            instructions="版本一指令",
            change_note="初始版本",
            actor="admin",
        )
        await registry.transition(draft.skill_id, 1, "submit_review", actor="reviewer")
        await registry.transition(draft.skill_id, 1, "publish", actor="admin")
        selected = await registry.published_skills(
            ToolContext(visit_matter_id="visit-1", idempotency_key="turn-1")
        )
    finally:
        await registry.close()

    restarted = PostgresSkillRegistry.from_url(DATABASE_URL or "")
    try:
        listed = await restarted.list_skills()
        persisted = next(skill for skill in listed if skill["slug"] == slug)
        audits = await restarted.list_audits()
        selection = next(
            audit
            for audit in audits
            if audit.skill_id == draft.skill_id and audit.action == "runtime_select"
        )
    finally:
        await restarted.close()

    versions = persisted["versions"]
    assert isinstance(versions, list)
    first_version = versions[0]
    assert isinstance(first_version, dict)
    assert first_version["status"] == "published"
    assert await selected[0].load_instructions() == "版本一指令"
    assert selection.turn_id == "turn-1"
