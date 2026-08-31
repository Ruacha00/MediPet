from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import uuid4

from medipet.agent.capabilities import SkillDefinition, ToolContext
from medipet.skills.archive import (
    SkillArchiveError,
    SkillResource,
    export_skill_archive,
    parse_skill_archive,
    static_publish_blockers,
    validate_skill_content,
)

if TYPE_CHECKING:
    from medipet.tools.registry import ToolRegistry

SkillStatus = Literal["draft", "in_review", "published", "retired", "quarantined"]
LifecycleAction = Literal["submit_review", "publish", "retire", "activate"]


class SkillRegistryError(ValueError):
    pass


class SkillNotFoundError(SkillRegistryError):
    pass


class SkillTransitionError(SkillRegistryError):
    pass


@dataclass(frozen=True)
class SkillVersion:
    skill_id: str
    version: int
    slug: str
    name: str
    description: str
    instructions: str
    change_note: str
    status: SkillStatus
    active: bool
    created_at: datetime
    resources: tuple[SkillResource, ...] = ()
    governance: dict[str, object] = field(default_factory=dict)
    quarantine_reasons: tuple[str, ...] = ()
    publish_blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "change_note": self.change_note,
            "status": self.status,
            "active": self.active,
            "created_at": self.created_at,
            "resources": [resource.to_dict() for resource in self.resources],
            "governance": dict(self.governance),
            "quarantine_reasons": list(self.quarantine_reasons),
            "publish_blockers": list(self.publish_blockers),
        }


@dataclass(frozen=True)
class SkillAudit:
    action: str
    skill_id: str | None
    version: int | None
    actor: str
    created_at: datetime
    visit_matter_id: str | None = None
    turn_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SkillRegistry(Protocol):
    async def list_skills(self) -> list[dict[str, object]]: ...

    async def create_skill(
        self,
        *,
        slug: str,
        name: str,
        description: str,
        instructions: str,
        change_note: str,
        skill_type: Literal["instruction-only", "tool-assisted"] = "instruction-only",
        actor: str,
    ) -> SkillVersion: ...

    async def edit_skill(
        self,
        skill_id: str,
        *,
        instructions: str,
        change_note: str,
        actor: str,
        name: str | None = None,
        description: str | None = None,
    ) -> SkillVersion: ...

    async def transition(
        self,
        skill_id: str,
        version: int,
        action: LifecycleAction,
        *,
        actor: str,
    ) -> SkillVersion: ...

    async def list_audits(self) -> list[SkillAudit]: ...

    async def import_package(self, payload: bytes, *, actor: str) -> SkillVersion: ...

    async def export_package(
        self, skill_id: str, version: int, *, actor: str
    ) -> tuple[str, bytes]: ...

    async def record_rejection(self, action: str, *, actor: str) -> None: ...

    async def published_skills(self, context: ToolContext) -> tuple[SkillDefinition, ...]: ...

    async def assert_tool_bindings_mutable(self, skill_id: str, version: int) -> None: ...


class InMemorySkillRegistry:
    def __init__(self, *, tool_registry: ToolRegistry | None = None) -> None:
        self._versions: dict[str, list[SkillVersion]] = {}
        self._audits: list[SkillAudit] = []
        self._tool_registry = tool_registry

    async def list_skills(self) -> list[dict[str, object]]:
        return [
            {
                "skill_id": skill_id,
                "slug": versions[0].slug,
                "name": versions[-1].name,
                "description": versions[-1].description,
                "versions": [version.to_dict() for version in versions],
            }
            for skill_id, versions in self._versions.items()
        ]

    async def create_skill(
        self,
        *,
        slug: str,
        name: str,
        description: str,
        instructions: str,
        change_note: str,
        skill_type: Literal["instruction-only", "tool-assisted"] = "instruction-only",
        actor: str,
    ) -> SkillVersion:
        normalized_slug = validate_skill_slug(slug)
        normalized_name = required_text(name, "Skill 名称不能为空")
        normalized_description = required_text(description, "Skill 描述不能为空")
        normalized_instructions = required_text(instructions, "Skill 指令不能为空")
        normalized_change_note = required_text(change_note, "变更说明不能为空")
        if any(versions[0].slug == normalized_slug for versions in self._versions.values()):
            raise SkillRegistryError("Skill slug 已存在")
        governance = {
            "format_version": 1,
            "display_name": normalized_name,
            "change_note": normalized_change_note,
            "risk_level": "standard",
            "required_approvals": 0,
            "skill_type": skill_type,
        }
        validate_skill_content(
            slug=normalized_slug,
            description=normalized_description,
            instructions=normalized_instructions,
            governance=governance,
        )
        version = SkillVersion(
            skill_id=f"skill-{uuid4().hex}",
            version=1,
            slug=normalized_slug,
            name=normalized_name,
            description=normalized_description,
            instructions=normalized_instructions,
            change_note=normalized_change_note,
            status="draft",
            active=False,
            created_at=datetime.now(UTC),
            governance=governance,
            publish_blockers=static_publish_blockers({"SKILL.md": normalized_instructions}),
        )
        self._versions[version.skill_id] = [version]
        self._audit("create", version, actor)
        return version

    async def edit_skill(
        self,
        skill_id: str,
        *,
        instructions: str,
        change_note: str,
        actor: str,
        name: str | None = None,
        description: str | None = None,
    ) -> SkillVersion:
        versions = self._require_skill(skill_id)
        source = versions[-1]
        if source.status == "quarantined":
            raise SkillTransitionError("隔离的 Skill 版本不能编辑")
        normalized_instructions = required_text(instructions, "Skill 指令不能为空")
        normalized_change_note = required_text(change_note, "变更说明不能为空")
        normalized_name = (
            source.name if name is None else required_text(name, "Skill 名称不能为空")
        )
        normalized_description = (
            source.description
            if description is None
            else required_text(description, "Skill 描述不能为空")
        )
        governance = {
            **source.governance,
            "display_name": normalized_name,
            "change_note": normalized_change_note,
        }
        validate_skill_content(
            slug=source.slug,
            description=normalized_description,
            instructions=normalized_instructions,
            governance=governance,
            resources=source.resources,
        )
        version = SkillVersion(
            skill_id=skill_id,
            version=source.version + 1,
            slug=source.slug,
            name=normalized_name,
            description=normalized_description,
            instructions=normalized_instructions,
            change_note=normalized_change_note,
            status="draft",
            active=False,
            created_at=datetime.now(UTC),
            resources=source.resources,
            governance=governance,
            publish_blockers=publish_blockers_for(normalized_instructions, source.resources),
        )
        versions.append(version)
        self._audit("edit", version, actor)
        return version

    async def transition(
        self,
        skill_id: str,
        version: int,
        action: LifecycleAction,
        *,
        actor: str,
    ) -> SkillVersion:
        versions = self._require_skill(skill_id)
        index = next((i for i, item in enumerate(versions) if item.version == version), None)
        if index is None:
            raise SkillNotFoundError("Skill 版本不存在")
        current = versions[index]
        if current.status == "quarantined":
            self._audit("reject_transition", current, actor)
            raise SkillTransitionError("隔离的 Skill 版本不能进入审核或发布流程")
        if action == "publish" and current.publish_blockers:
            reasons = "；".join(current.publish_blockers)
            self._audit("reject_publish", current, actor)
            raise SkillTransitionError(f"Skill 未通过静态发布检查：{reasons}")
        if action == "publish" and current.governance.get("skill_type") == "tool-assisted":
            if self._tool_registry is None:
                self._audit("reject_publish", current, actor)
                raise SkillTransitionError("tool-assisted Skill requires a Tool binding")
            try:
                await self._tool_registry.validate_bindings(skill_id, version)
            except ValueError as error:
                self._audit("reject_publish", current, actor)
                raise SkillTransitionError(str(error)) from error
        target_status, active = transition_target(current.status, action)
        if action == "publish" and self._tool_registry is not None:
            await self._tool_registry.freeze_bindings(skill_id, version)
        if action in {"publish", "activate"}:
            self._deactivate_other_versions(versions, version, actor)
        updated = replace(current, status=target_status, active=active)
        versions[index] = updated
        self._audit(action, updated, actor)
        return updated

    async def list_audits(self) -> list[SkillAudit]:
        return list(self._audits)

    async def import_package(self, payload: bytes, *, actor: str) -> SkillVersion:
        try:
            package = parse_skill_archive(payload)
            normalized_slug = validate_skill_slug(package.slug)
            if any(versions[0].slug == normalized_slug for versions in self._versions.values()):
                raise SkillRegistryError("Skill slug 已存在，导入不能覆盖现有版本")
        except (SkillArchiveError, SkillRegistryError):
            self._audit_action("reject_import", actor=actor)
            raise

        status: SkillStatus = "quarantined" if package.quarantine_reasons else "draft"
        version = SkillVersion(
            skill_id=f"skill-{uuid4().hex}",
            version=1,
            slug=normalized_slug,
            name=package.name,
            description=package.description,
            instructions=package.instructions,
            change_note=package.change_note,
            status=status,
            active=False,
            created_at=datetime.now(UTC),
            resources=package.resources,
            governance=package.governance,
            quarantine_reasons=package.quarantine_reasons,
            publish_blockers=package.publish_blockers,
        )
        self._versions[version.skill_id] = [version]
        self._audit("import", version, actor)
        if status == "quarantined":
            self._audit("quarantine", version, actor)
        return version

    async def export_package(self, skill_id: str, version: int, *, actor: str) -> tuple[str, bytes]:
        versions = self._require_skill(skill_id)
        selected = next((item for item in versions if item.version == version), None)
        if selected is None:
            raise SkillNotFoundError("Skill 版本不存在")
        self._audit("export", selected, actor)
        return (
            f"{selected.slug}-v{selected.version}.zip",
            export_skill_archive(
                slug=selected.slug,
                description=selected.description,
                instructions=selected.instructions,
                governance=selected.governance,
                resources=selected.resources,
            ),
        )

    async def record_rejection(self, action: str, *, actor: str) -> None:
        self._audit_action(action, actor=actor)

    async def published_skills(self, context: ToolContext) -> tuple[SkillDefinition, ...]:
        selected = tuple(
            version
            for versions in self._versions.values()
            for version in versions
            if version.status == "published" and version.active
        )
        definitions: list[SkillDefinition] = []
        for version in selected:

            async def load_instructions(pinned: SkillVersion = version) -> str:
                return pinned.instructions

            definitions.append(
                SkillDefinition(
                    skill_id=version.skill_id,
                    slug=version.slug,
                    version=version.version,
                    name=version.name,
                    description=version.description,
                    load_instructions=load_instructions,
                )
            )
            self._audits.append(
                SkillAudit(
                    action="runtime_select",
                    skill_id=version.skill_id,
                    version=version.version,
                    actor="runtime",
                    created_at=datetime.now(UTC),
                    visit_matter_id=context.visit_matter_id,
                    turn_id=context.idempotency_key,
                )
            )
        return tuple(definitions)

    async def assert_tool_bindings_mutable(self, skill_id: str, version: int) -> None:
        versions = self._require_skill(skill_id)
        selected = next((item for item in versions if item.version == version), None)
        if selected is None:
            raise SkillNotFoundError("Skill version does not exist")
        if selected.status in {"published", "retired"}:
            raise SkillTransitionError("Published Skill Tool bindings are immutable")

    def _require_skill(self, skill_id: str) -> list[SkillVersion]:
        try:
            return self._versions[skill_id]
        except KeyError:
            raise SkillNotFoundError("Skill 不存在") from None

    def _deactivate_other_versions(
        self,
        versions: list[SkillVersion],
        selected: int,
        actor: str,
    ) -> None:
        for index, version in enumerate(versions):
            if version.version != selected and version.active:
                retired = replace(version, status="retired", active=False)
                versions[index] = retired
                self._audit("retire", retired, actor)

    def _audit(self, action: str, version: SkillVersion, actor: str) -> None:
        self._audit_action(
            action,
            actor=actor,
            skill_id=version.skill_id,
            version=version.version,
        )

    def _audit_action(
        self,
        action: str,
        *,
        actor: str,
        skill_id: str | None = None,
        version: int | None = None,
    ) -> None:
        self._audits.append(
            SkillAudit(
                action=action,
                skill_id=skill_id,
                version=version,
                actor=actor,
                created_at=datetime.now(UTC),
            )
        )


def required_text(value: str, message: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise SkillRegistryError(message)
    return normalized


def publish_blockers_for(
    instructions: str, resources: tuple[SkillResource, ...]
) -> tuple[str, ...]:
    files = {"SKILL.md": instructions}
    files.update(
        {
            resource.path: resource.content.decode("utf-8")
            for resource in resources
            if resource.media_type.startswith("text/") or resource.media_type.endswith("json")
        }
    )
    return static_publish_blockers(files)


def validate_skill_slug(slug: str) -> str:
    normalized = required_text(slug, "Skill slug 不能为空").lower()
    if re.match(r"^(?:safety-?policy|platform)(?:$|[-._/])", normalized):
        raise SkillRegistryError("Skill 使用了受保护的命名空间")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized):
        raise SkillRegistryError("Skill slug 只能包含小写字母、数字和单个连字符")
    return normalized


def transition_target(status: str, action: LifecycleAction) -> tuple[SkillStatus, bool]:
    if action == "submit_review" and status == "draft":
        return "in_review", False
    if action == "publish" and status == "in_review":
        return "published", True
    if action == "retire" and status == "published":
        return "retired", False
    if action == "activate" and status in {"published", "retired"}:
        return "published", True
    raise SkillTransitionError("Skill 生命周期转换无效")
