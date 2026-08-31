from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from medipet.hospital.bootstrap import (
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
    sources = load_hospital_skill_sources()
    changed_sources = (
        replace(sources[0], instructions=f"{sources[0].instructions}\n\nUse the revised flow."),
        *sources[1:],
    )

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
    sources = load_hospital_skill_sources()
    changed_sources = (
        replace(sources[0], instructions=f"{sources[0].instructions}\n\nUse the revised flow."),
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
