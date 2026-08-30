from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import cast

from medipet.skills.archive import parse_skill_manifest
from medipet.skills.registry import SkillRegistry, SkillStatus, SkillVersion
from medipet.tools.registry import ToolProvider, ToolRegistry


@dataclass(frozen=True)
class HospitalSkillSource:
    slug: str
    name: str
    description: str
    instructions: str
    change_note: str


def load_hospital_skill_source() -> HospitalSkillSource:
    package = files("medipet.hospital.appointment_skill")
    slug, description, instructions = parse_skill_manifest(
        package.joinpath("SKILL.md").read_text(encoding="utf-8")
    )
    metadata = json.loads(package.joinpath("medipet.json").read_text(encoding="utf-8"))
    return HospitalSkillSource(
        slug=slug,
        name=cast(str, metadata["display_name"]),
        description=description,
        instructions=instructions,
        change_note=cast(str, metadata["change_note"]),
    )


async def bootstrap_development_hospital_skill(
    skill_registry: SkillRegistry,
    tool_registry: ToolRegistry,
    tool_provider: ToolProvider,
) -> SkillVersion | dict[str, object]:
    source = load_hospital_skill_source()
    tools = await tool_provider.tools()
    desired_bindings = {(tool.tool_id, tool.version) for tool in tools}
    await tool_registry.synchronize(tools, actor="development-bootstrap")
    for tool in tools:
        await tool_registry.configure(
            tool.tool_id,
            tool.version,
            enabled=True,
            approval_required=tool.approval_required,
            actor="development-bootstrap",
        )

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
            latest["instructions"] == source.instructions
            and latest["status"] == "published"
            and latest["active"] is True
            and current_bindings == desired_bindings
        ):
            return latest
        immutable_binding_mismatch = (
            status in {"published", "retired"}
            and current_bindings != desired_bindings
        )
        has_unexpected_binding = not current_bindings.issubset(desired_bindings)
        if (
            latest["instructions"] != source.instructions
            or immutable_binding_mismatch
            or has_unexpected_binding
        ):
            selected = await skill_registry.edit_skill(
                skill_id,
                instructions=source.instructions,
                change_note=source.change_note,
                actor="development-bootstrap",
            )
            current_bindings = set()
        else:
            selected = latest

    skill_id = _field(selected, "skill_id", str)
    version = _field(selected, "version", int)
    status = cast(SkillStatus, _field(selected, "status", str))
    if status == "retired":
        return await skill_registry.transition(
            skill_id, version, "activate", actor="development-bootstrap"
        )
    if status == "published":
        return await skill_registry.transition(
            skill_id, version, "activate", actor="development-bootstrap"
        )

    for tool in tools:
        if (tool.tool_id, tool.version) in current_bindings:
            continue
        await tool_registry.bind(
            skill_id,
            version,
            tool.tool_id,
            tool.version,
            actor="development-bootstrap",
        )
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
