from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from medipet.agent.capabilities import VisitStage
from medipet.capability_files import hospital_tool_manifest_path
from medipet.tools.registry import ToolEffect, ToolRegistryError


@dataclass(frozen=True)
class HospitalToolSpec:
    tool_id: str
    version: str
    name: str
    description: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    effect: ToolEffect
    approval_required: bool
    allowed_stages: tuple[VisitStage, ...]
    confirmation_schema: dict[str, object] | None


def load_hospital_tool_specs(path: Path | None = None) -> tuple[HospitalToolSpec, ...]:
    manifest_path = path or hospital_tool_manifest_path()
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ToolRegistryError(f"Cannot load Tool manifest {manifest_path}: {error}") from error
    if not isinstance(raw, dict) or raw.get("format_version") != 1:
        raise ToolRegistryError("Hospital Tool manifest format_version must be 1")
    definitions = raw.get("$defs", {})
    tools = raw.get("tools")
    if not isinstance(definitions, dict) or not isinstance(tools, list):
        raise ToolRegistryError("Hospital Tool manifest must contain $defs and tools")
    return tuple(_parse_tool(item, definitions) for item in tools)


def _parse_tool(raw: object, definitions: dict[str, object]) -> HospitalToolSpec:
    if not isinstance(raw, dict):
        raise ToolRegistryError("Each hospital Tool manifest entry must be an object")
    effect = _text(raw, "effect")
    if effect not in {"read", "write"}:
        raise ToolRegistryError("Hospital Tool effect must be read or write")
    approval_required = raw.get("approval_required")
    if not isinstance(approval_required, bool):
        raise ToolRegistryError("Hospital Tool approval_required must be boolean")
    stages = raw.get("allowed_stages", ["pre_visit", "in_visit"])
    if not isinstance(stages, list) or not stages or any(
        stage not in {"pre_visit", "in_visit"} for stage in stages
    ):
        raise ToolRegistryError("Hospital Tool allowed_stages are invalid")
    confirmation_raw = raw.get("confirmation_schema")
    confirmation_schema = (
        None
        if confirmation_raw is None
        else _schema(confirmation_raw, definitions, "confirmation_schema")
    )
    if (effect == "write") != (confirmation_schema is not None):
        raise ToolRegistryError("Write hospital Tools require a confirmation schema")
    return HospitalToolSpec(
        tool_id=_text(raw, "tool_id"),
        version=_text(raw, "version"),
        name=_text(raw, "name"),
        description=_text(raw, "description"),
        input_schema=_schema(raw.get("input_schema"), definitions, "input_schema"),
        output_schema=_schema(raw.get("output_schema"), definitions, "output_schema"),
        effect=cast(Literal["read", "write"], effect),
        approval_required=approval_required,
        allowed_stages=cast(tuple[VisitStage, ...], tuple(stages)),
        confirmation_schema=confirmation_schema,
    )


def _schema(raw: object, definitions: dict[str, object], field: str) -> dict[str, object]:
    resolved = _resolve_references(raw, definitions, ())
    if not isinstance(resolved, dict) or resolved.get("type") != "object":
        raise ToolRegistryError(f"Hospital Tool {field} must describe an object")
    return resolved


def _resolve_references(
    value: object,
    definitions: dict[str, object],
    resolving: tuple[str, ...],
) -> object:
    if isinstance(value, list):
        return [_resolve_references(item, definitions, resolving) for item in value]
    if not isinstance(value, dict):
        return value
    reference = value.get("$ref")
    if reference is not None:
        if (
            len(value) != 1
            or not isinstance(reference, str)
            or not reference.startswith("#/$defs/")
        ):
            raise ToolRegistryError("Only local $defs Tool schema references are supported")
        name = reference.removeprefix("#/$defs/")
        if name in resolving:
            raise ToolRegistryError("Cyclic Tool schema reference")
        if name not in definitions:
            raise ToolRegistryError(f"Unknown Tool schema reference: {name}")
        return _resolve_references(definitions[name], definitions, (*resolving, name))
    return {
        key: _resolve_references(item, definitions, resolving)
        for key, item in value.items()
    }


def _text(raw: Mapping[str, object], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolRegistryError(f"Hospital Tool {name} must be non-empty text")
    return value.strip()
