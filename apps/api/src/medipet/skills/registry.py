from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from medipet.agent.capabilities import SkillDefinition, ToolContext

SkillStatus = Literal["draft", "in_review", "published", "retired"]
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

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SkillAudit:
    action: str
    skill_id: str
    version: int
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
        actor: str,
    ) -> SkillVersion: ...

    async def edit_skill(
        self,
        skill_id: str,
        *,
        instructions: str,
        change_note: str,
        actor: str,
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

    async def published_skills(self, context: ToolContext) -> tuple[SkillDefinition, ...]: ...


class InMemorySkillRegistry:
    def __init__(self) -> None:
        self._versions: dict[str, list[SkillVersion]] = {}
        self._audits: list[SkillAudit] = []

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
        actor: str,
    ) -> SkillVersion:
        normalized_slug = validate_skill_slug(slug)
        if any(versions[0].slug == normalized_slug for versions in self._versions.values()):
            raise SkillRegistryError("Skill slug 已存在")
        version = SkillVersion(
            skill_id=f"skill-{uuid4().hex}",
            version=1,
            slug=normalized_slug,
            name=required_text(name, "Skill 名称不能为空"),
            description=required_text(description, "Skill 描述不能为空"),
            instructions=required_text(instructions, "Skill 指令不能为空"),
            change_note=required_text(change_note, "变更说明不能为空"),
            status="draft",
            active=False,
            created_at=datetime.now(UTC),
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
    ) -> SkillVersion:
        versions = self._require_skill(skill_id)
        source = versions[-1]
        version = SkillVersion(
            skill_id=skill_id,
            version=source.version + 1,
            slug=source.slug,
            name=source.name,
            description=source.description,
            instructions=required_text(instructions, "Skill 指令不能为空"),
            change_note=required_text(change_note, "变更说明不能为空"),
            status="draft",
            active=False,
            created_at=datetime.now(UTC),
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
        target_status, active = transition_target(current.status, action)
        if action in {"publish", "activate"}:
            self._deactivate_other_versions(versions, version, actor)
        updated = SkillVersion(**{**current.to_dict(), "status": target_status, "active": active})
        versions[index] = updated
        self._audit(action, updated, actor)
        return updated

    async def list_audits(self) -> list[SkillAudit]:
        return list(self._audits)

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
                retired = SkillVersion(
                    **{**version.to_dict(), "status": "retired", "active": False}
                )
                versions[index] = retired
                self._audit("retire", retired, actor)

    def _audit(self, action: str, version: SkillVersion, actor: str) -> None:
        self._audits.append(
            SkillAudit(
                action=action,
                skill_id=version.skill_id,
                version=version.version,
                actor=actor,
                created_at=datetime.now(UTC),
            )
        )


def required_text(value: str, message: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise SkillRegistryError(message)
    return normalized


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
