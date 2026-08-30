from __future__ import annotations

from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient

from medipet.agent.capabilities import SkillDefinition, ToolContext
from medipet.delivery.http import create_app
from medipet.skills.capabilities import RegistryCapabilityProvider
from medipet.skills.registry import InMemorySkillRegistry
from medipet.tools.registry import (
    InMemoryToolRegistry,
    TrustedTool,
)


async def _execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
    return {"echo": arguments["query"], "visit_matter_id": context.visit_matter_id}


def _trusted_tool(*, version: str = "1", description: str = "Test lookup") -> TrustedTool:
    return TrustedTool(
        tool_id="test.lookup",
        version=version,
        name="test_lookup",
        description=description,
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"echo": {"type": "string"}},
        },
        effect="read",
        approval_required=False,
        execute=_execute,
    )


@pytest.mark.asyncio
async def test_empty_tool_registry_and_trusted_provider_are_valid() -> None:
    registry = InMemoryToolRegistry()
    await registry.synchronize((), actor="deployment")
    app = create_app(
        tool_registry=registry,
        management_token="management-secret",
        environment="development",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/v1/admin/tools",
            headers={"Authorization": "Bearer management-secret"},
        )

    assert response.status_code == 200
    assert response.json() == {"tools": []}


@pytest.mark.asyncio
async def test_admin_can_view_and_govern_synced_tool_but_cannot_supply_code_or_url() -> None:
    registry = InMemoryToolRegistry()
    await registry.synchronize((_trusted_tool(),), actor="deployment")
    app = create_app(
        tool_registry=registry,
        management_token="management-secret",
        environment="development",
    )
    headers = {"Authorization": "Bearer management-secret"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/v1/admin/tools", headers=headers)
        governed = await client.patch(
            "/v1/admin/tools/test.lookup/versions/1",
            headers=headers,
            json={"enabled": True, "approval_required": True},
        )
        rejected = await client.patch(
            "/v1/admin/tools/test.lookup/versions/1",
            headers=headers,
            json={"enabled": True, "implementation": "print('bad')", "url": "https://bad"},
        )

    tool = listed.json()["tools"][0]["versions"][0]
    assert tool["tool_id"] == "test.lookup"
    assert tool["name"] == "test_lookup"
    assert tool["output_schema"]["type"] == "object"
    assert tool["effect"] == "read"
    assert tool["available"] is True
    assert governed.status_code == 200
    assert governed.json()["enabled"] is True
    assert governed.json()["approval_required"] is True
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_sync_records_new_versions_and_marks_disappeared_implementations() -> None:
    registry = InMemoryToolRegistry()
    await registry.synchronize((_trusted_tool(),), actor="deployment")
    await registry.synchronize(
        (_trusted_tool(version="2", description="Updated lookup"),), actor="deployment"
    )
    await registry.synchronize((), actor="deployment")

    tools = await registry.list_tools()
    audits = await registry.list_audits()

    versions = cast(list[dict[str, object]], tools[0]["versions"])
    assert [item["version"] for item in versions] == ["1", "2"]
    assert all(item["available"] is False for item in versions)
    assert [audit.action for audit in audits] == [
        "sync",
        "version",
        "missing",
        "missing",
    ]


@pytest.mark.asyncio
async def test_sync_rejects_contract_drift_under_an_existing_version() -> None:
    registry = InMemoryToolRegistry()
    await registry.synchronize((_trusted_tool(),), actor="deployment")

    with pytest.raises(ValueError, match="new version"):
        await registry.synchronize(
            (_trusted_tool(description="Changed without a version"),), actor="deployment"
        )

    assert [audit.action for audit in await registry.list_audits()] == [
        "sync",
        "reject_sync",
    ]

    registry = InMemoryToolRegistry()
    await registry.synchronize((_trusted_tool(),), actor="deployment")
    changed_approval = TrustedTool(
        **{**_trusted_tool().__dict__, "approval_required": True}
    )
    with pytest.raises(ValueError, match="new version"):
        await registry.synchronize((changed_approval,), actor="deployment")


@pytest.mark.asyncio
async def test_tool_assisted_skill_requires_an_enabled_compatible_binding_to_publish() -> None:
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await tools.synchronize((_trusted_tool(),), actor="deployment")
    await tools.configure(
        "test.lookup",
        "1",
        enabled=True,
        approval_required=False,
        actor="development-admin",
    )
    app = create_app(
        skill_registry=skills,
        tool_registry=tools,
        management_token="management-secret",
        environment="development",
    )
    headers = {"Authorization": "Bearer management-secret"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/v1/admin/skills",
            headers=headers,
            json={
                "slug": "lookup-helper",
                "name": "Lookup helper",
                "description": "Uses a trusted lookup",
                "instructions": "Use the bound lookup Tool.",
                "change_note": "Initial version",
                "skill_type": "tool-assisted",
            },
        )
        skill_id = created.json()["skill_id"]
        await client.post(f"/v1/admin/skills/{skill_id}/versions/1/submit-review", headers=headers)
        blocked = await client.post(
            f"/v1/admin/skills/{skill_id}/versions/1/publish", headers=headers
        )
        bound = await client.post(
            f"/v1/admin/skills/{skill_id}/versions/1/tool-bindings",
            headers=headers,
            json={"tool_id": "test.lookup", "tool_version": "1"},
        )
        published = await client.post(
            f"/v1/admin/skills/{skill_id}/versions/1/publish", headers=headers
        )
        immutable = await client.post(
            f"/v1/admin/skills/{skill_id}/versions/1/tool-bindings",
            headers=headers,
            json={"tool_id": "test.lookup", "tool_version": "1"},
        )

    assert blocked.status_code == 409
    assert "binding" in blocked.json()["detail"]
    assert bound.status_code == 201
    assert published.status_code == 200
    assert immutable.status_code == 409


@pytest.mark.asyncio
async def test_registry_capability_snapshot_intersects_binding_enablement_and_authorization() -> (
    None
):
    executions: list[str] = []

    async def execute(arguments: dict[str, object], context: ToolContext) -> dict[str, object]:
        executions.append(context.participant_id)
        return {"echo": arguments["query"]}

    trusted = _trusted_tool()
    trusted = TrustedTool(
        **{
            **trusted.__dict__,
            "execute": execute,
            "authorize": lambda context: context.participant_id == "participant-1",
            "allowed_stages": ("in_visit",),
        }
    )
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await tools.synchronize((trusted,), actor="deployment")
    await tools.configure("test.lookup", "1", enabled=True, approval_required=False, actor="admin")
    skill = await skills.create_skill(
        slug="lookup-helper",
        name="Lookup helper",
        description="Uses lookup",
        instructions="Use lookup.",
        change_note="Initial",
        skill_type="tool-assisted",
        actor="admin",
    )
    await tools.bind(skill.skill_id, 1, "test.lookup", "1", actor="admin")
    await skills.transition(skill.skill_id, 1, "submit_review", actor="admin")
    await skills.transition(skill.skill_id, 1, "publish", actor="admin")
    provider = RegistryCapabilityProvider(skills, tools)

    allowed = await provider.snapshot(
        ToolContext(participant_id="participant-1", visit_stage="in_visit")
    )
    denied = await provider.snapshot(ToolContext(participant_id="participant-2"))
    denied_stage = await provider.snapshot(ToolContext(participant_id="participant-1"))

    assert [tool.name for tool in allowed.tools if tool.enabled] == ["test_lookup"]
    assert [tool.name for tool in denied.tools if tool.enabled] == []
    assert [tool.name for tool in denied_stage.tools if tool.enabled] == []
    assert executions == []
    assert allowed.record_unknown_tool_rejection is not None
    await allowed.record_unknown_tool_rejection(
        "rogue_tool", ToolContext(visit_matter_id="visit-1", idempotency_key="turn-1")
    )
    rejected = next(
        audit for audit in await tools.list_audits() if audit.action == "reject_invoke"
    )
    assert rejected.tool_id == "rogue_tool"


@pytest.mark.asyncio
async def test_registry_preserves_write_tool_confirmation_contract() -> None:
    async def execute(
        arguments: dict[str, object], context: ToolContext
    ) -> dict[str, object]:
        del arguments, context
        return {"saved": True}

    async def prepare_confirmation(
        arguments: dict[str, object], context: ToolContext
    ) -> dict[str, object]:
        return {"value": arguments["value"], "patient_id": context.patient_id}

    async def revalidate_confirmation(
        arguments: dict[str, object],
        confirmation: dict[str, object],
        context: ToolContext,
    ) -> bool:
        return confirmation == {
            "value": arguments["value"],
            "patient_id": context.patient_id,
        }

    confirmation_schema = {
        "type": "object",
        "properties": {
            "value": {"type": "string"},
            "patient_id": {"type": "string"},
        },
        "required": ["value", "patient_id"],
        "additionalProperties": False,
    }
    trusted = TrustedTool(
        tool_id="test.write",
        version="1",
        name="test_write",
        description="Test prepared write",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        confirmation_schema=confirmation_schema,
        effect="write",
        approval_required=True,
        execute=execute,
        prepare_confirmation=prepare_confirmation,
        revalidate_confirmation=revalidate_confirmation,
    )
    registry = InMemoryToolRegistry()
    await registry.synchronize((trusted,), actor="deployment")
    await registry.configure(
        "test.write", "1", enabled=True, approval_required=True, actor="admin"
    )
    await registry.bind("skill-1", 1, "test.write", "1", actor="admin")

    async def load_instructions() -> str:
        return "Use the write Tool."

    tools = await registry.runtime_tools(
        (
            SkillDefinition(
                skill_id="skill-1",
                slug="test-write",
                version=1,
                name="Test write",
                description="Test write",
                load_instructions=load_instructions,
            ),
        ),
        ToolContext(patient_id="patient-1"),
    )

    assert tools[0].confirmation_schema == confirmation_schema
    assert tools[0].prepare_confirmation is prepare_confirmation
    assert tools[0].revalidate_confirmation is revalidate_confirmation
