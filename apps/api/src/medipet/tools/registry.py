from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Literal, Protocol

from medipet.agent.capabilities import (
    SkillDefinition,
    ToolAuthorizer,
    ToolConfirmationContract,
    ToolContext,
    ToolDefinition,
    ToolExecutor,
    ToolPresenter,
    VisitStage,
    _allow,
)

ToolEffect = Literal["read", "write"]


@dataclass(frozen=True)
class TrustedTool:
    tool_id: str
    version: str
    name: str
    description: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    effect: ToolEffect
    approval_required: bool
    execute: ToolExecutor
    confirmation_contract: ToolConfirmationContract | None = None
    present: ToolPresenter | None = None
    authorize: ToolAuthorizer = _allow
    allowed_stages: tuple[VisitStage, ...] = ("pre_visit", "in_visit")

    def contract(self) -> dict[str, object]:
        return {
            "tool_id": self.tool_id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "confirmation_schema": (
                dict(self.confirmation_contract.schema)
                if self.confirmation_contract is not None
                else None
            ),
            "effect": self.effect,
            "allowed_stages": list(self.allowed_stages),
            "provider_approval_required": self.approval_required,
        }


class ToolProvider(Protocol):
    async def tools(self) -> tuple[TrustedTool, ...]: ...


class StaticToolProvider:
    def __init__(self, tools: tuple[TrustedTool, ...] = ()) -> None:
        self._tools = tools

    async def tools(self) -> tuple[TrustedTool, ...]:
        return self._tools


@dataclass(frozen=True)
class ToolVersion:
    tool: TrustedTool
    enabled: bool = False
    available: bool = True
    approval_required: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            **self.tool.contract(),
            "enabled": self.enabled,
            "available": self.available,
            "approval_required": self.approval_required,
        }


@dataclass(frozen=True)
class ToolAudit:
    action: str
    actor: str
    created_at: datetime
    tool_id: str | None = None
    version: str | None = None
    visit_matter_id: str | None = None
    turn_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ToolRegistryError(ValueError):
    pass


class ToolNotFoundError(ToolRegistryError):
    pass


class ToolRegistry(Protocol):
    async def synchronize(self, tools: tuple[TrustedTool, ...], *, actor: str) -> None: ...
    async def list_tools(self) -> list[dict[str, object]]: ...
    async def configure(
        self, tool_id: str, version: str, *, enabled: bool, approval_required: bool, actor: str
    ) -> ToolVersion: ...
    async def bind(
        self, skill_id: str, skill_version: int, tool_id: str, tool_version: str, *, actor: str
    ) -> dict[str, object]: ...
    async def binding_versions(
        self, skill_id: str, skill_version: int
    ) -> tuple[tuple[str, str], ...]: ...
    async def unbind(
        self, skill_id: str, skill_version: int, tool_id: str, tool_version: str, *, actor: str
    ) -> dict[str, object]: ...
    async def validate_bindings(self, skill_id: str, skill_version: int) -> None: ...

    async def freeze_bindings(self, skill_id: str, skill_version: int) -> None: ...
    async def runtime_tools(
        self, skills: tuple[SkillDefinition, ...], context: ToolContext
    ) -> tuple[ToolDefinition, ...]: ...
    async def list_audits(self) -> list[ToolAudit]: ...

    async def record_management_rejection(
        self, action: str, tool_id: str, version: str, *, actor: str
    ) -> None: ...

    async def record_unknown_rejection(
        self, tool_name: str, context: ToolContext
    ) -> None: ...


class InMemoryToolRegistry:
    def __init__(self) -> None:
        self._versions: dict[str, list[ToolVersion]] = {}
        self._audits: list[ToolAudit] = []
        self._bindings: dict[tuple[str, int], list[tuple[str, str]]] = {}
        self._frozen_bindings: set[tuple[str, int]] = set()

    async def synchronize(self, tools: tuple[TrustedTool, ...], *, actor: str) -> None:
        try:
            _validate_provider_tools(tools)
        except ToolRegistryError:
            rejected = tools[0] if tools else None
            self._audit(
                "reject_sync",
                actor,
                rejected.tool_id if rejected else "",
                rejected.version if rejected else "",
            )
            raise
        supplied: set[tuple[str, str]] = set()
        for tool in tools:
            key = (tool.tool_id, tool.version)
            supplied.add(key)
            versions = self._versions.setdefault(tool.tool_id, [])
            existing_index = next(
                (index for index, item in enumerate(versions) if item.tool.version == tool.version),
                None,
            )
            if existing_index is not None:
                existing = versions[existing_index]
                if existing.tool.contract() != tool.contract():
                    self._audit("reject_sync", actor, tool.tool_id, tool.version)
                    raise ToolRegistryError(
                        "Tool contract changed under an existing version; deploy a new version"
                    )
                versions[existing_index] = replace(existing, tool=tool, available=True)
                continue
            action = "sync" if not versions else "version"
            versions.append(
                ToolVersion(
                    tool=tool,
                    approval_required=tool.approval_required,
                )
            )
            self._audit(action, actor, tool.tool_id, tool.version)

        for tool_id, versions in self._versions.items():
            for index, version in enumerate(versions):
                key = (tool_id, version.tool.version)
                if version.available and key not in supplied:
                    versions[index] = replace(version, available=False)
                    self._audit("missing", actor, tool_id, version.tool.version)

    async def list_tools(self) -> list[dict[str, object]]:
        return [
            {
                "tool_id": tool_id,
                "versions": [version.to_dict() for version in versions],
            }
            for tool_id, versions in self._versions.items()
        ]

    async def configure(
        self,
        tool_id: str,
        version: str,
        *,
        enabled: bool,
        approval_required: bool,
        actor: str,
    ) -> ToolVersion:
        versions = self._require(tool_id)
        index = next(
            (index for index, item in enumerate(versions) if item.tool.version == version),
            None,
        )
        if index is None:
            raise ToolNotFoundError("Tool version does not exist")
        current = versions[index]
        if current.tool.effect == "write" and not approval_required:
            self._audit("reject_configure", actor, tool_id, version)
            raise ToolRegistryError("Write Tool approval cannot be disabled")
        if enabled and not current.available:
            self._audit("reject_enable", actor, tool_id, version)
            raise ToolRegistryError("Missing Tool implementation cannot be enabled")
        updated = replace(
            current,
            enabled=enabled,
            approval_required=approval_required,
        )
        versions[index] = updated
        self._audit("enable" if enabled else "disable", actor, tool_id, version)
        if approval_required != current.approval_required:
            self._audit("configure_approval", actor, tool_id, version)
        return updated

    async def resolve(self, tool_id: str, version: str) -> ToolVersion:
        versions = self._require(tool_id)
        selected = next((item for item in versions if item.tool.version == version), None)
        if selected is None:
            raise ToolNotFoundError("Tool version does not exist")
        return selected

    async def bind(
        self,
        skill_id: str,
        skill_version: int,
        tool_id: str,
        tool_version: str,
        *,
        actor: str,
    ) -> dict[str, object]:
        selected = await self.resolve(tool_id, tool_version)
        key = (skill_id, skill_version)
        if key in self._frozen_bindings:
            raise ToolRegistryError("Published Skill Tool bindings are immutable")
        binding = (tool_id, tool_version)
        bindings = self._bindings.setdefault(key, [])
        if binding not in bindings:
            bindings.append(binding)
            self._audit("bind", actor, tool_id, tool_version)
        return {
            "skill_id": skill_id,
            "skill_version": skill_version,
            "tool_id": selected.tool.tool_id,
            "tool_version": selected.tool.version,
        }

    async def validate_bindings(self, skill_id: str, skill_version: int) -> None:
        bindings = self._bindings.get((skill_id, skill_version), [])
        if not bindings:
            raise ToolRegistryError("tool-assisted Skill requires a Tool binding")
        for tool_id, version in bindings:
            selected = await self.resolve(tool_id, version)
            if not selected.available or not selected.enabled:
                raise ToolRegistryError(
                    "tool-assisted Skill binding must reference an enabled compatible Tool version"
                )

    async def binding_versions(
        self, skill_id: str, skill_version: int
    ) -> tuple[tuple[str, str], ...]:
        return tuple(self._bindings.get((skill_id, skill_version), ()))

    async def unbind(
        self,
        skill_id: str,
        skill_version: int,
        tool_id: str,
        tool_version: str,
        *,
        actor: str,
    ) -> dict[str, object]:
        key = (skill_id, skill_version)
        if key in self._frozen_bindings:
            self._audit("reject_unbind", actor, tool_id, tool_version)
            raise ToolRegistryError("Only draft Skill Tool bindings can be changed")
        binding = (tool_id, tool_version)
        bindings = self._bindings.get(key, [])
        if binding not in bindings:
            self._audit("reject_unbind", actor, tool_id, tool_version)
            raise ToolNotFoundError("Tool binding does not exist")
        bindings.remove(binding)
        self._audit("unbind", actor, tool_id, tool_version)
        return {
            "skill_id": skill_id,
            "skill_version": skill_version,
            "tool_id": tool_id,
            "tool_version": tool_version,
        }

    async def freeze_bindings(self, skill_id: str, skill_version: int) -> None:
        self._frozen_bindings.add((skill_id, skill_version))

    async def runtime_tools(
        self, skills: tuple[SkillDefinition, ...], context: ToolContext
    ) -> tuple[ToolDefinition, ...]:
        definitions: list[ToolDefinition] = []
        definition_indexes: dict[tuple[str, str], int] = {}
        for skill in skills:
            for tool_id, version in self._bindings.get((skill.skill_id, skill.version), []):
                key = (tool_id, version)
                existing_index = definition_indexes.get(key)
                if existing_index is not None:
                    existing = definitions[existing_index]
                    definitions[existing_index] = replace(
                        existing,
                        required_skill_ids=(*existing.required_skill_ids, skill.skill_id),
                    )
                    continue
                selected = await self.resolve(tool_id, version)
                tool = selected.tool

                async def execute(
                    arguments: dict[str, object],
                    execution_context: ToolContext,
                    pinned: TrustedTool = tool,
                ) -> dict[str, object]:
                    try:
                        result = await pinned.execute(arguments, execution_context)
                    except Exception:
                        await self.record_invocation(
                            "reject_invoke", pinned.tool_id, pinned.version, execution_context
                        )
                        raise
                    await self.record_invocation(
                        "invoke", pinned.tool_id, pinned.version, execution_context
                    )
                    return result

                async def record_rejection(
                    rejection_context: ToolContext,
                    pinned: TrustedTool = tool,
                ) -> None:
                    await self.record_invocation(
                        "reject_invoke", pinned.tool_id, pinned.version, rejection_context
                    )

                async def revalidate(
                    validation_context: ToolContext,
                    pinned: TrustedTool = tool,
                ) -> bool:
                    current = await self.resolve(pinned.tool_id, pinned.version)
                    return (
                        current.enabled
                        and current.available
                        and validation_context.visit_stage in pinned.allowed_stages
                        and pinned.authorize(validation_context)
                    )

                authorized = (
                    selected.enabled
                    and selected.available
                    and context.visit_stage in tool.allowed_stages
                    and tool.authorize(context)
                )
                definitions.append(
                    ToolDefinition(
                        name=tool.name,
                        version=tool.version,
                        description=tool.description,
                        input_schema=tool.input_schema,
                        output_schema=tool.output_schema,
                        confirmation_contract=tool.confirmation_contract,
                        effect=tool.effect,
                        execute=execute,
                        tool_id=tool.tool_id,
                        approval_required=selected.approval_required,
                        enabled=authorized,
                        bound=True,
                        authorize=tool.authorize,
                        record_rejection=record_rejection,
                        revalidate=revalidate,
                        present=tool.present,
                        required_skill_ids=(skill.skill_id,),
                    )
                )
                definition_indexes[key] = len(definitions) - 1
        return tuple(definitions)

    async def record_invocation(
        self, action: str, tool_id: str, version: str, context: ToolContext
    ) -> None:
        self._audits.append(
            ToolAudit(
                action=action,
                actor="runtime",
                created_at=datetime.now(UTC),
                tool_id=tool_id,
                version=version,
                visit_matter_id=context.visit_matter_id,
                turn_id=context.idempotency_key,
            )
        )

    async def record_unknown_rejection(
        self, tool_name: str, context: ToolContext
    ) -> None:
        self._audits.append(
            ToolAudit(
                action="reject_invoke",
                actor="runtime",
                created_at=datetime.now(UTC),
                tool_id=tool_name,
                visit_matter_id=context.visit_matter_id,
                turn_id=context.idempotency_key,
            )
        )

    async def list_audits(self) -> list[ToolAudit]:
        return list(self._audits)

    async def record_management_rejection(
        self, action: str, tool_id: str, version: str, *, actor: str
    ) -> None:
        self._audit(action, actor, tool_id, version)

    def _require(self, tool_id: str) -> list[ToolVersion]:
        try:
            return self._versions[tool_id]
        except KeyError:
            raise ToolNotFoundError("Tool does not exist") from None

    def _audit(self, action: str, actor: str, tool_id: str, version: str) -> None:
        self._audits.append(
            ToolAudit(
                action=action,
                actor=actor,
                created_at=datetime.now(UTC),
                tool_id=tool_id,
                version=version,
            )
        )


def _validate_trusted_tool(tool: TrustedTool) -> None:
    if not tool.tool_id.strip() or not tool.version.strip() or not tool.name.strip():
        raise ToolRegistryError("Tool identity fields cannot be empty")
    if tool.input_schema.get("type") != "object":
        raise ToolRegistryError("Tool input Schema must describe an object")
    if tool.output_schema.get("type") != "object":
        raise ToolRegistryError("Tool output Schema must describe an object")
    if tool.confirmation_contract is not None:
        if tool.effect != "write":
            raise ToolRegistryError("Prepared confirmations require a write Tool")
        if tool.confirmation_contract.schema.get("type") != "object":
            raise ToolRegistryError("Tool confirmation Schema must describe an object")
    if tool.effect == "write" and not tool.approval_required:
        raise ToolRegistryError("Write Tools must require approval")


def _validate_provider_tools(tools: tuple[TrustedTool, ...]) -> None:
    identities: set[tuple[str, str]] = set()
    names: set[str] = set()
    for tool in tools:
        _validate_trusted_tool(tool)
        identity = (tool.tool_id, tool.version)
        if identity in identities or tool.name in names:
            raise ToolRegistryError("ToolProvider returned duplicate Tool contracts")
        identities.add(identity)
        names.add(tool.name)
