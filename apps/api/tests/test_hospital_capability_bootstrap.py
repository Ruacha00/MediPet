from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from medipet.hospital.bootstrap import (
    HospitalSkillSource,
    bootstrap_development_hospital_skill,
    load_hospital_skill_sources,
)
from medipet.hospital.data_source import FakeHospitalDataSource
from medipet.hospital.fake import FakeHospitalOperations
from medipet.hospital.tools import HospitalToolProvider
from medipet.skills.registry import InMemorySkillRegistry
from medipet.tools.registry import InMemoryToolRegistry, StaticToolProvider


def _provider() -> HospitalToolProvider:
    return HospitalToolProvider(
        FakeHospitalOperations(
            FakeHospitalDataSource.load_default(),
            clock=lambda: datetime(2026, 8, 31, 8, tzinfo=UTC),
        )
    )


def _changed_sources() -> tuple[HospitalSkillSource, ...]:
    sources = load_hospital_skill_sources()
    return (
        replace(sources[0], instructions=f"{sources[0].instructions}\n\nUse the revised flow."),
        *sources[1:],
    )


def test_wayfinding_skill_has_only_the_three_read_contracts() -> None:
    source = next(
        item
        for item in load_hospital_skill_sources()
        if item.slug == "hospital-wayfinding"
    )

    assert source.tool_bindings == (
        "hospital.list_wayfinding_origins",
        "hospital.list_service_locations",
        "hospital.get_wayfinding_guidance",
    )
    assert "不得根据症状选择科室" in source.instructions
    assert "不得生成、拼接、反转或改写" in source.instructions


def test_cancellation_skill_separates_request_from_proposal_confirmation() -> None:
    source = next(
        item
        for item in load_hospital_skill_sources()
        if item.slug == "hospital-appointment-cancellation"
    )

    assert "已经表达取消意图" in source.instructions
    assert "立即调用 `hospital_cancel_appointment` 生成确认提案" in source.instructions
    assert "不要先用文字再次询问" in source.instructions
    assert "最终确认是独立的第二阶段" in source.instructions


@pytest.mark.asyncio
async def test_empty_registries_receive_published_enabled_development_defaults() -> None:
    provider = _provider()
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)

    await bootstrap_development_hospital_skill(skills, tools, provider)

    listed_tools = await tools.list_tools()
    assert listed_tools
    assert all(
        version["enabled"] is True
        for item in listed_tools
        for version in cast(list[dict[str, object]], item["versions"])
    )


@pytest.mark.asyncio
async def test_new_bundled_capability_is_activated_in_an_existing_development_registry() -> None:
    provider = _provider()
    deployed_tools = await provider.tools()
    old_tools = tuple(
        tool
        for tool in deployed_tools
        if tool.tool_id
        not in {
            "hospital.list_wayfinding_origins",
            "hospital.list_service_locations",
            "hospital.get_wayfinding_guidance",
        }
    )
    sources = load_hospital_skill_sources()
    old_sources = tuple(
        source for source in sources if source.slug != "hospital-wayfinding"
    )
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await bootstrap_development_hospital_skill(
        skills,
        tools,
        StaticToolProvider(old_tools),
        skill_sources=old_sources,
    )

    await bootstrap_development_hospital_skill(skills, tools, provider)

    wayfinding = next(
        item for item in await skills.list_skills() if item["slug"] == "hospital-wayfinding"
    )
    version = cast(list[dict[str, object]], wayfinding["versions"])[-1]
    assert version["status"] == "published"
    assert version["active"] is True
    for tool_id in (
        "hospital.list_wayfinding_origins",
        "hospital.list_service_locations",
        "hospital.get_wayfinding_guidance",
    ):
        assert (await tools.resolve(tool_id, "1")).enabled is True
    listed_skills = await skills.list_skills()
    assert listed_skills
    assert all(
        cast(list[dict[str, object]], item["versions"])[-1]["status"] == "published"
        and cast(list[dict[str, object]], item["versions"])[-1]["active"] is True
        for item in listed_skills
    )


@pytest.mark.asyncio
async def test_reconciliation_preserves_disabled_tools_and_retired_skills() -> None:
    provider = _provider()
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await bootstrap_development_hospital_skill(skills, tools, provider)
    trusted_tool = (await provider.tools())[0]
    await tools.configure(
        trusted_tool.tool_id,
        trusted_tool.version,
        enabled=False,
        approval_required=trusted_tool.approval_required,
        actor="development-admin",
    )
    listed = await skills.list_skills()
    selected = listed[0]
    selected_versions = cast(list[dict[str, object]], selected["versions"])
    await skills.transition(
        cast(str, selected["skill_id"]),
        cast(int, selected_versions[-1]["version"]),
        "retire",
        actor="development-admin",
    )

    await bootstrap_development_hospital_skill(skills, tools, provider)

    assert (await tools.resolve(trusted_tool.tool_id, trusted_tool.version)).enabled is False
    refreshed = await skills.list_skills()
    refreshed_versions = cast(list[dict[str, object]], refreshed[0]["versions"])
    assert refreshed_versions[-1]["status"] == "retired"
    assert refreshed_versions[-1]["active"] is False


@pytest.mark.asyncio
async def test_reconciliation_stages_new_tool_and_skill_versions_without_publishing() -> None:
    provider = _provider()
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await bootstrap_development_hospital_skill(skills, tools, provider)
    trusted_tools = await provider.tools()
    changed_tool = trusted_tools[0]
    changed_provider = StaticToolProvider(
        tuple(
            replace(tool, version="2") if tool.tool_id == changed_tool.tool_id else tool
            for tool in trusted_tools
        )
    )
    changed_sources = _changed_sources()

    await bootstrap_development_hospital_skill(
        skills,
        tools,
        changed_provider,
        skill_sources=changed_sources,
    )

    assert (await tools.resolve(changed_tool.tool_id, "2")).enabled is False
    listed = await skills.list_skills()
    changed = next(item for item in listed if item["slug"] == changed_sources[0].slug)
    versions = cast(list[dict[str, object]], changed["versions"])
    assert versions[-1]["version"] == 2
    assert versions[-1]["status"] == "draft"
    assert versions[-1]["active"] is False


@pytest.mark.asyncio
async def test_reconciliation_preserves_an_explicit_skill_rollback() -> None:
    provider = _provider()
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await bootstrap_development_hospital_skill(skills, tools, provider)
    changed_sources = _changed_sources()
    await bootstrap_development_hospital_skill(
        skills,
        tools,
        provider,
        skill_sources=changed_sources,
    )
    listed = await skills.list_skills()
    changed = next(item for item in listed if item["slug"] == changed_sources[0].slug)
    skill_id = cast(str, changed["skill_id"])
    await skills.transition(skill_id, 2, "submit_review", actor="development-admin")
    await skills.transition(skill_id, 2, "publish", actor="development-admin")
    await skills.transition(skill_id, 1, "activate", actor="development-admin")

    await bootstrap_development_hospital_skill(
        skills,
        tools,
        provider,
        skill_sources=changed_sources,
    )

    refreshed = await skills.list_skills()
    versions = cast(
        list[dict[str, object]],
        next(item for item in refreshed if item["skill_id"] == skill_id)["versions"],
    )
    assert versions[0]["status"] == "published"
    assert versions[0]["active"] is True
    assert versions[1]["status"] == "retired"
    assert versions[1]["active"] is False


@pytest.mark.asyncio
async def test_reconciliation_versions_skill_name_and_description_changes() -> None:
    provider = _provider()
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await bootstrap_development_hospital_skill(skills, tools, provider)
    sources = load_hospital_skill_sources()
    changed_sources = (
        replace(
            sources[0],
            name="Revised hospital capability",
            description="Revised discovery description",
        ),
        *sources[1:],
    )

    await bootstrap_development_hospital_skill(
        skills,
        tools,
        provider,
        skill_sources=changed_sources,
    )

    listed = await skills.list_skills()
    changed = next(item for item in listed if item["slug"] == changed_sources[0].slug)
    versions = cast(list[dict[str, object]], changed["versions"])
    assert len(versions) == 2
    assert versions[-1]["name"] == "Revised hospital capability"
    assert versions[-1]["description"] == "Revised discovery description"
    assert cast(dict[str, object], versions[-1]["governance"])["display_name"] == (
        "Revised hospital capability"
    )
    assert versions[-1]["status"] == "draft"


@pytest.mark.parametrize(
    ("submit_for_review", "previous_status"),
    ((False, "draft"), (True, "in_review")),
)
@pytest.mark.asyncio
async def test_reconciliation_versions_existing_unpublished_binding_changes(
    submit_for_review: bool,
    previous_status: str,
) -> None:
    provider = _provider()
    tools = InMemoryToolRegistry()
    skills = InMemorySkillRegistry(tool_registry=tools)
    await bootstrap_development_hospital_skill(skills, tools, provider)
    sources = load_hospital_skill_sources()
    listed = await skills.list_skills()
    selected = next(item for item in listed if item["slug"] == sources[0].slug)
    skill_id = cast(str, selected["skill_id"])
    versions = cast(list[dict[str, object]], selected["versions"])
    draft = await skills.edit_skill(
        skill_id,
        instructions=cast(str, versions[-1]["instructions"]),
        change_note="prepare review fixture",
        actor="development-admin",
    )
    if submit_for_review:
        await skills.transition(
            skill_id,
            draft.version,
            "submit_review",
            actor="development-admin",
        )

    await bootstrap_development_hospital_skill(skills, tools, provider)

    refreshed = await skills.list_skills()
    refreshed_versions = cast(
        list[dict[str, object]],
        next(item for item in refreshed if item["skill_id"] == skill_id)["versions"],
    )
    assert [version["status"] for version in refreshed_versions] == [
        "published",
        previous_status,
        "draft",
    ]
    assert await tools.binding_versions(skill_id, 2) == ()
    assert set(await tools.binding_versions(skill_id, 3)) == {
        (tool.tool_id, tool.version)
        for tool in await provider.tools()
        if tool.tool_id in sources[0].tool_bindings
    }
