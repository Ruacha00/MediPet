from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from medipet.capability_files import hospital_skill_directory, hospital_skills_directory
from medipet.skills.archive import parse_skill_manifest
from medipet.skills.registry import SkillRegistry, SkillStatus, SkillVersion
from medipet.tools.registry import ToolProvider, ToolRegistry, TrustedTool


@dataclass(frozen=True)
class HospitalSkillSource:
    slug: str
    name: str
    description: str
    instructions: str
    change_note: str
    tool_bindings: tuple[str, ...]


def load_hospital_skill_source(directory: Path | None = None) -> HospitalSkillSource:
    package = directory or hospital_skill_directory()
    slug, description, instructions = parse_skill_manifest(
        package.joinpath("SKILL.md").read_text(encoding="utf-8")
    )
    metadata = json.loads(package.joinpath("medipet.json").read_text(encoding="utf-8"))
    raw_bindings = metadata.get("tool_bindings")
    if not isinstance(raw_bindings, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_bindings
    ):
        raise ValueError(f"医院 Skill {slug} 缺少有效的 tool_bindings")
    tool_bindings = tuple(item.strip() for item in raw_bindings)
    if len(tool_bindings) != len(set(tool_bindings)):
        raise ValueError(f"医院 Skill {slug} 的 tool_bindings 不能重复")
    return HospitalSkillSource(
        slug=slug,
        name=cast(str, metadata["display_name"]),
        description=description,
        instructions=instructions,
        change_note=cast(str, metadata["change_note"]),
        tool_bindings=tool_bindings,
    )


def load_hospital_skill_sources(directory: Path | None = None) -> tuple[HospitalSkillSource, ...]:
    root = directory or hospital_skills_directory()
    packages = sorted(
        path
        for path in root.iterdir()
        if path.is_dir()
        and path.joinpath("SKILL.md").is_file()
        and path.joinpath("medipet.json").is_file()
    )
    if not packages:
        raise ValueError("未找到外置医院 Skills")
    sources = tuple(load_hospital_skill_source(package) for package in packages)
    slugs = [source.slug for source in sources]
    if len(slugs) != len(set(slugs)):
        raise ValueError("外置医院 Skill slug 不能重复")
    return sources


async def bootstrap_development_hospital_skill(
    skill_registry: SkillRegistry,
    tool_registry: ToolRegistry,
    tool_provider: ToolProvider,
    *,
    skill_sources: tuple[HospitalSkillSource, ...] | None = None,
) -> tuple[SkillVersion | dict[str, object], ...]:
    sources = load_hospital_skill_sources() if skill_sources is None else skill_sources
    tools = await tool_provider.tools()
    tools_by_id = {tool.tool_id: tool for tool in tools}
    provision_defaults = (
        not await skill_registry.list_skills() and not await tool_registry.list_tools()
    )
    await tool_registry.synchronize(tools, actor="development-bootstrap")
    if provision_defaults:
        for tool in tools:
            await tool_registry.configure(
                tool.tool_id,
                tool.version,
                enabled=True,
                approval_required=tool.approval_required,
                actor="development-bootstrap",
            )

    unknown_bindings = {
        tool_id
        for source in sources
        for tool_id in source.tool_bindings
        if tool_id not in tools_by_id
    }
    if unknown_bindings:
        names = ", ".join(sorted(unknown_bindings))
        raise ValueError(f"医院 Skill 绑定了未部署的 Tool: {names}")

    results = []
    for source in sources:
        desired_bindings = {
            (tools_by_id[tool_id].tool_id, tools_by_id[tool_id].version)
            for tool_id in source.tool_bindings
        }
        results.append(
            await _bootstrap_hospital_skill_source(
                source,
                desired_bindings,
                skill_registry,
                tool_registry,
                tools_by_id,
                provision_defaults=provision_defaults,
            )
        )
    return tuple(results)


async def _bootstrap_hospital_skill_source(
    source: HospitalSkillSource,
    desired_bindings: set[tuple[str, str]],
    skill_registry: SkillRegistry,
    tool_registry: ToolRegistry,
    tools_by_id: dict[str, TrustedTool],
    *,
    provision_defaults: bool,
) -> SkillVersion | dict[str, object]:
    listed = await skill_registry.list_skills()
    existing = next((item for item in listed if item["slug"] == source.slug), None)
    current_bindings: set[tuple[str, str]] = set()
    if existing is None:
        selected: SkillVersion | dict[str, object] = await skill_registry.create_skill(
            slug=source.slug,
            name=source.name,
            description=source.description,
            instructions=source.instructions,
            change_note=source.change_note,
            skill_type="tool-assisted",
            actor="development-bootstrap",
        )
    else:
        versions = cast(list[dict[str, object]], existing["versions"])
        latest = versions[-1]
        skill_id = cast(str, existing["skill_id"])
        version = cast(int, latest["version"])
        status = cast(SkillStatus, latest["status"])
        if status == "quarantined":
            raise ValueError("开发医院 Skill 处于隔离状态，不能自动激活")
        current_bindings = set(await tool_registry.binding_versions(skill_id, version))
        if (
            latest["name"] == source.name
            and latest["description"] == source.description
            and latest["instructions"] == source.instructions
            and current_bindings == desired_bindings
        ):
            return latest
        reviewed_binding_mismatch = status != "draft" and current_bindings != desired_bindings
        has_unexpected_binding = not current_bindings.issubset(desired_bindings)
        if (
            latest["name"] != source.name
            or latest["description"] != source.description
            or latest["instructions"] != source.instructions
            or reviewed_binding_mismatch
            or has_unexpected_binding
        ):
            selected = await skill_registry.edit_skill(
                skill_id,
                instructions=source.instructions,
                change_note=source.change_note,
                actor="development-bootstrap",
                name=source.name,
                description=source.description,
            )
            current_bindings = set()
        else:
            selected = latest

    skill_id = _field(selected, "skill_id", str)
    version = _field(selected, "version", int)
    status = cast(SkillStatus, _field(selected, "status", str))

    for tool_id in source.tool_bindings:
        tool = tools_by_id[tool_id]
        if (tool.tool_id, tool.version) in current_bindings:
            continue
        await tool_registry.bind(
            skill_id,
            version,
            tool.tool_id,
            tool.version,
            actor="development-bootstrap",
        )
    if not provision_defaults or status in {"published", "retired"}:
        return selected
    if status == "draft":
        selected = await skill_registry.transition(
            skill_id,
            version,
            "submit_review",
            actor="development-bootstrap",
        )
    return await skill_registry.transition(
        skill_id,
        version,
        "publish",
        actor="development-bootstrap",
    )


def _field[T](
    value: SkillVersion | dict[str, object],
    name: str,
    expected: type[T],
) -> T:
    raw: object = getattr(value, name) if isinstance(value, SkillVersion) else value[name]
    if not isinstance(raw, expected):
        raise TypeError(f"医院 Skill {name} 类型无效")
    return raw
