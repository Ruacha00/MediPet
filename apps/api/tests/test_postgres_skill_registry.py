from __future__ import annotations

import asyncio
import io
import json
import os
import zipfile
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from medipet.agent.capabilities import ToolContext
from medipet.skills.postgres import PostgresSkillRegistry
from medipet.tools.postgres import PostgresToolRegistry
from medipet.tools.registry import ToolRegistryError, TrustedTool

DATABASE_URL = os.getenv("MEDIPET_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="MEDIPET_TEST_DATABASE_URL is required for PostgreSQL contract tests",
)


async def _upgrade_database() -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", DATABASE_URL or "")
    await asyncio.to_thread(command.upgrade, config, "head")


async def _execute(
    arguments: dict[str, object], context: ToolContext
) -> dict[str, object]:
    del context
    return arguments


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


@pytest.mark.asyncio
async def test_postgres_skill_registry_persists_and_exports_package_resources() -> None:
    await _upgrade_database()
    slug = f"archive-round-trip-{uuid4().hex}"
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "SKILL.md",
            f"---\nname: {slug}\ndescription: 归档往返\n---\n\n加载参考清单。\n",
        )
        archive.writestr(
            "medipet.json",
            json.dumps(
                {
                    "format_version": 1,
                    "display_name": "归档往返",
                    "change_note": "持久化资源",
                    "risk_level": "low",
                    "required_approvals": 0,
                },
                ensure_ascii=False,
            ),
        )
        archive.writestr("references/checklist.md", "# 检查清单\n")

    registry = PostgresSkillRegistry.from_url(DATABASE_URL or "")
    try:
        imported = await registry.import_package(package.getvalue(), actor="admin")
    finally:
        await registry.close()

    restarted = PostgresSkillRegistry.from_url(DATABASE_URL or "")
    try:
        listed = await restarted.list_skills()
        persisted = next(skill for skill in listed if skill["slug"] == slug)
        resource = await restarted.get_resource(
            imported.skill_id, 1, "references/checklist.md"
        )
        _, exported = await restarted.export_package(imported.skill_id, 1, actor="admin")
    finally:
        await restarted.close()

    versions = persisted["versions"]
    assert isinstance(versions, list)
    version = versions[0]
    assert isinstance(version, dict)
    assert version["resources"] == [
        {"path": "references/checklist.md", "media_type": "text/markdown", "size": 15}
    ]
    assert resource.content.decode() == "# 检查清单\n"
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        assert archive.read("references/checklist.md").decode() == "# 检查清单\n"


@pytest.mark.asyncio
async def test_postgres_tool_bindings_freeze_when_skill_enters_review() -> None:
    await _upgrade_database()
    tools = PostgresToolRegistry.from_url(DATABASE_URL or "")
    skills = PostgresSkillRegistry.from_url(DATABASE_URL or "", tool_registry=tools)
    tool_id = f"hospital.contract-{uuid4().hex}"
    trusted = TrustedTool(
        tool_id=tool_id,
        version="1",
        name="contract_test",
        description="PostgreSQL binding contract",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        effect="read",
        approval_required=False,
        execute=_execute,
    )
    try:
        await tools.synchronize((trusted,), actor="provider")
        await tools.configure(
            tool_id, "1", enabled=True, approval_required=False, actor="admin"
        )
        draft = await skills.create_skill(
            slug=f"binding-freeze-{uuid4().hex}",
            name="绑定冻结",
            description="验证审核后绑定不可变",
            instructions="仅使用已绑定的 Tool。",
            change_note="初始版本",
            skill_type="tool-assisted",
            actor="admin",
        )
        await tools.bind(draft.skill_id, 1, tool_id, "1", actor="admin")
        assert await tools.binding_versions(draft.skill_id, 1) == ((tool_id, "1"),)

        await skills.transition(draft.skill_id, 1, "submit_review", actor="reviewer")

        with pytest.raises(ToolRegistryError, match="Only draft"):
            await tools.unbind(draft.skill_id, 1, tool_id, "1", actor="admin")
        with pytest.raises(ToolRegistryError, match="Only draft"):
            await tools.bind(draft.skill_id, 1, tool_id, "1", actor="admin")
        await skills.transition(draft.skill_id, 1, "publish", actor="admin")
    finally:
        await skills.close()
        await tools.close()
