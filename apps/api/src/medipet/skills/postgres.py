from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from medipet.agent.capabilities import SkillDefinition, ToolContext
from medipet.persistence.models import (
    SkillAuditRecord,
    SkillRecord,
    SkillResourceRecord,
    SkillVersionRecord,
)
from medipet.persistence.postgres import postgres_async_url
from medipet.skills.archive import (
    SkillArchiveError,
    SkillResource,
    export_skill_archive,
    parse_skill_archive,
    static_publish_blockers,
    validate_skill_content,
)
from medipet.skills.registry import (
    LifecycleAction,
    SkillAudit,
    SkillNotFoundError,
    SkillRegistryError,
    SkillStatus,
    SkillTransitionError,
    SkillVersion,
    publish_blockers_for,
    required_text,
    transition_target,
    validate_skill_slug,
)


class PostgresSkillRegistry:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(cls, url: str) -> PostgresSkillRegistry:
        return cls(create_async_engine(postgres_async_url(url), pool_pre_ping=True))

    async def close(self) -> None:
        await self._engine.dispose()

    async def list_skills(self) -> list[dict[str, object]]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(SkillRecord, SkillVersionRecord)
                    .join(SkillVersionRecord, SkillVersionRecord.skill_id == SkillRecord.id)
                    .order_by(SkillRecord.created_at, SkillVersionRecord.version)
                )
            ).all()
            resources = (
                await session.scalars(
                    select(SkillResourceRecord).order_by(SkillResourceRecord.path)
                )
            ).all()
        resources_by_version: dict[str, list[SkillResourceRecord]] = defaultdict(list)
        for resource in resources:
            resources_by_version[resource.skill_version_id].append(resource)
        grouped: dict[str, list[tuple[SkillRecord, SkillVersionRecord]]] = defaultdict(list)
        for skill, version in rows:
            grouped[skill.id].append((skill, version))
        return [
            {
                "skill_id": skill_id,
                "slug": pairs[0][0].slug,
                "name": pairs[-1][1].name,
                "description": pairs[-1][1].description,
                "versions": [
                    self._to_version(
                        skill, version, resources_by_version.get(version.id, [])
                    ).to_dict()
                    for skill, version in pairs
                ],
            }
            for skill_id, pairs in grouped.items()
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
        normalized_name = required_text(name, "Skill 名称不能为空")
        normalized_description = required_text(description, "Skill 描述不能为空")
        normalized_instructions = required_text(instructions, "Skill 指令不能为空")
        normalized_change_note = required_text(change_note, "变更说明不能为空")
        governance = {
            "format_version": 1,
            "display_name": normalized_name,
            "change_note": normalized_change_note,
            "risk_level": "standard",
            "required_approvals": 0,
        }
        validate_skill_content(
            slug=normalized_slug,
            description=normalized_description,
            instructions=normalized_instructions,
            governance=governance,
        )
        skill_id = f"skill-{uuid4().hex}"
        async with self._sessions.begin() as session:
            if await session.scalar(
                select(SkillRecord.id).where(SkillRecord.slug == normalized_slug)
            ):
                raise SkillRegistryError("Skill slug 已存在")
            skill = SkillRecord(id=skill_id, slug=normalized_slug)
            record = SkillVersionRecord(
                id=f"skill-version-{uuid4().hex}",
                skill_id=skill_id,
                version=1,
                name=normalized_name,
                description=normalized_description,
                instructions=normalized_instructions,
                change_note=normalized_change_note,
                status="draft",
                active=False,
                governance=governance,
                quarantine_reasons=[],
                publish_blockers=list(
                    static_publish_blockers({"SKILL.md": normalized_instructions})
                ),
            )
            session.add_all((skill, record))
            await session.flush()
            self._add_audit(session, "create", record, actor)
        return self._to_version(skill, record)

    async def edit_skill(
        self,
        skill_id: str,
        *,
        instructions: str,
        change_note: str,
        actor: str,
    ) -> SkillVersion:
        async with self._sessions.begin() as session:
            skill = await self._require_skill(session, skill_id)
            source = await session.scalar(
                select(SkillVersionRecord)
                .where(SkillVersionRecord.skill_id == skill_id)
                .order_by(SkillVersionRecord.version.desc())
                .limit(1)
                .with_for_update()
            )
            if source is None:
                raise SkillNotFoundError("Skill 版本不存在")
            if source.status == "quarantined":
                raise SkillTransitionError("隔离的 Skill 版本不能编辑")
            source_resources = (
                await session.scalars(
                    select(SkillResourceRecord)
                    .where(SkillResourceRecord.skill_version_id == source.id)
                    .order_by(SkillResourceRecord.path)
                )
            ).all()
            copied_skill_resources = tuple(
                SkillResource(
                    path=resource.path,
                    media_type=resource.media_type,
                    content=resource.content,
                )
                for resource in source_resources
            )
            normalized_instructions = required_text(instructions, "Skill 指令不能为空")
            normalized_change_note = required_text(change_note, "变更说明不能为空")
            governance = {
                **source.governance,
                "change_note": normalized_change_note,
            }
            validate_skill_content(
                slug=skill.slug,
                description=source.description,
                instructions=normalized_instructions,
                governance=governance,
                resources=copied_skill_resources,
            )
            record = SkillVersionRecord(
                id=f"skill-version-{uuid4().hex}",
                skill_id=skill_id,
                version=source.version + 1,
                name=source.name,
                description=source.description,
                instructions=normalized_instructions,
                change_note=normalized_change_note,
                status="draft",
                active=False,
                governance=governance,
                quarantine_reasons=[],
                publish_blockers=list(
                    publish_blockers_for(normalized_instructions, copied_skill_resources)
                ),
            )
            session.add(record)
            await session.flush()
            copied_resources = [
                SkillResourceRecord(
                    id=f"skill-resource-{uuid4().hex}",
                    skill_version_id=record.id,
                    path=resource.path,
                    media_type=resource.media_type,
                    content=resource.content,
                )
                for resource in source_resources
            ]
            session.add_all(copied_resources)
            self._add_audit(session, "edit", record, actor)
        return self._to_version(skill, record, copied_resources)

    async def transition(
        self,
        skill_id: str,
        version: int,
        action: LifecycleAction,
        *,
        actor: str,
    ) -> SkillVersion:
        async with self._sessions.begin() as session:
            skill = await self._require_skill(session, skill_id)
            records = list(
                (
                    await session.scalars(
                        select(SkillVersionRecord)
                        .where(SkillVersionRecord.skill_id == skill_id)
                        .order_by(SkillVersionRecord.version)
                        .with_for_update()
                    )
                ).all()
            )
            record = next((item for item in records if item.version == version), None)
            if record is None:
                raise SkillNotFoundError("Skill 版本不存在")
            if record.status == "quarantined":
                self._add_audit(session, "reject_transition", record, actor)
                return_error = SkillTransitionError(
                    "隔离的 Skill 版本不能进入审核或发布流程"
                )
            elif action == "publish" and record.publish_blockers:
                self._add_audit(session, "reject_publish", record, actor)
                reasons = "；".join(record.publish_blockers)
                return_error = SkillTransitionError(f"Skill 未通过静态发布检查：{reasons}")
            else:
                return_error = None
            if return_error is None:
                status, active = transition_target(record.status, action)
                if active:
                    for previous in records:
                        if previous.version != version and previous.active:
                            previous.status = "retired"
                            previous.active = False
                            self._add_audit(session, "retire", previous, actor)
                record.status = status
                record.active = active
                self._add_audit(session, action, record, actor)
            await session.flush()
        if return_error is not None:
            raise return_error
        return self._to_version(skill, record)

    async def list_audits(self) -> list[SkillAudit]:
        async with self._sessions() as session:
            records = (
                await session.scalars(
                    select(SkillAuditRecord).order_by(
                        SkillAuditRecord.created_at, SkillAuditRecord.id
                    )
                )
            ).all()
        return [self._to_audit(record) for record in records]

    async def import_package(self, payload: bytes, *, actor: str) -> SkillVersion:
        try:
            package = parse_skill_archive(payload)
            normalized_slug = validate_skill_slug(package.slug)
        except (SkillArchiveError, SkillRegistryError):
            await self._record_audit("reject_import", actor=actor)
            raise

        created: tuple[SkillRecord, SkillVersionRecord, list[SkillResourceRecord]] | None = None
        async with self._sessions.begin() as session:
            if await session.scalar(
                select(SkillRecord.id).where(SkillRecord.slug == normalized_slug)
            ):
                self._add_audit_action(session, "reject_import", actor=actor)
                duplicate = True
            else:
                duplicate = False
                skill = SkillRecord(id=f"skill-{uuid4().hex}", slug=normalized_slug)
                status: SkillStatus = (
                    "quarantined" if package.quarantine_reasons else "draft"
                )
                record = SkillVersionRecord(
                    id=f"skill-version-{uuid4().hex}",
                    skill_id=skill.id,
                    version=1,
                    name=package.name,
                    description=package.description,
                    instructions=package.instructions,
                    change_note=package.change_note,
                    status=status,
                    active=False,
                    governance=package.governance,
                    quarantine_reasons=list(package.quarantine_reasons),
                    publish_blockers=list(package.publish_blockers),
                )
                resource_records = [
                    SkillResourceRecord(
                        id=f"skill-resource-{uuid4().hex}",
                        skill_version_id=record.id,
                        path=resource.path,
                        media_type=resource.media_type,
                        content=resource.content,
                    )
                    for resource in package.resources
                ]
                session.add_all((skill, record, *resource_records))
                await session.flush()
                self._add_audit(session, "import", record, actor)
                if status == "quarantined":
                    self._add_audit(session, "quarantine", record, actor)
                created = (skill, record, resource_records)
        if duplicate:
            raise SkillRegistryError("Skill slug 已存在，导入不能覆盖现有版本")
        if created is None:
            raise RuntimeError("Skill 导入未创建版本")
        skill, record, resource_records = created
        return self._to_version(skill, record, resource_records)

    async def export_package(
        self, skill_id: str, version: int, *, actor: str
    ) -> tuple[str, bytes]:
        async with self._sessions.begin() as session:
            skill = await self._require_skill(session, skill_id)
            record = await session.scalar(
                select(SkillVersionRecord).where(
                    SkillVersionRecord.skill_id == skill_id,
                    SkillVersionRecord.version == version,
                )
            )
            if record is None:
                raise SkillNotFoundError("Skill 版本不存在")
            resource_records = (
                await session.scalars(
                    select(SkillResourceRecord)
                    .where(SkillResourceRecord.skill_version_id == record.id)
                    .order_by(SkillResourceRecord.path)
                )
            ).all()
            self._add_audit(session, "export", record, actor)
        selected = self._to_version(skill, record, resource_records)
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
        await self._record_audit(action, actor=actor)

    async def published_skills(self, context: ToolContext) -> tuple[SkillDefinition, ...]:
        async with self._sessions.begin() as session:
            rows = (
                await session.execute(
                    select(
                        SkillRecord.id,
                        SkillRecord.slug,
                        SkillVersionRecord.version,
                        SkillVersionRecord.name,
                        SkillVersionRecord.description,
                    )
                    .join(SkillVersionRecord, SkillVersionRecord.skill_id == SkillRecord.id)
                    .where(
                        SkillVersionRecord.status == "published",
                        SkillVersionRecord.active.is_(True),
                    )
                    .order_by(SkillRecord.slug)
                )
            ).all()
            definitions: list[SkillDefinition] = []
            for skill_id, slug, version, name, description in rows:

                async def load_instructions(
                    pinned_skill_id: str = skill_id,
                    pinned_version: int = version,
                ) -> str:
                    return await self._load_instructions(pinned_skill_id, pinned_version)

                definitions.append(
                    SkillDefinition(
                        slug=slug,
                        version=version,
                        name=name,
                        description=description,
                        load_instructions=load_instructions,
                    )
                )
                session.add(
                    SkillAuditRecord(
                        id=f"skill-audit-{uuid4().hex}",
                        action="runtime_select",
                        skill_id=skill_id,
                        version=version,
                        actor="runtime",
                        visit_matter_id=context.visit_matter_id or None,
                        turn_id=context.idempotency_key or None,
                    )
                )
        return tuple(definitions)

    async def _load_instructions(self, skill_id: str, version: int) -> str:
        async with self._sessions() as session:
            instructions = await session.scalar(
                select(SkillVersionRecord.instructions).where(
                    SkillVersionRecord.skill_id == skill_id,
                    SkillVersionRecord.version == version,
                )
            )
        if instructions is None:
            raise SkillNotFoundError("Skill 版本不存在")
        return instructions

    @staticmethod
    async def _require_skill(session: AsyncSession, skill_id: str) -> SkillRecord:
        skill = await session.get(SkillRecord, skill_id)
        if skill is None:
            raise SkillNotFoundError("Skill 不存在")
        return skill

    @staticmethod
    def _add_audit(
        session: AsyncSession, action: str, record: SkillVersionRecord, actor: str
    ) -> None:
        PostgresSkillRegistry._add_audit_action(
            session,
            action,
            actor=actor,
            skill_id=record.skill_id,
            version=record.version,
        )

    @staticmethod
    def _add_audit_action(
        session: AsyncSession,
        action: str,
        *,
        actor: str,
        skill_id: str | None = None,
        version: int | None = None,
    ) -> None:
        session.add(
            SkillAuditRecord(
                id=f"skill-audit-{uuid4().hex}",
                action=action,
                skill_id=skill_id,
                version=version,
                actor=actor,
            )
        )

    async def _record_audit(self, action: str, *, actor: str) -> None:
        async with self._sessions.begin() as session:
            self._add_audit_action(session, action, actor=actor)

    @staticmethod
    def _to_version(
        skill: SkillRecord,
        record: SkillVersionRecord,
        resources: Sequence[SkillResourceRecord] | None = None,
    ) -> SkillVersion:
        return SkillVersion(
            skill_id=skill.id,
            version=record.version,
            slug=skill.slug,
            name=record.name,
            description=record.description,
            instructions=record.instructions,
            change_note=record.change_note,
            status=cast(SkillStatus, record.status),
            active=record.active,
            created_at=record.created_at,
            resources=tuple(
                SkillResource(
                    path=resource.path,
                    media_type=resource.media_type,
                    content=resource.content,
                )
                for resource in resources or []
            ),
            governance=dict(record.governance),
            quarantine_reasons=tuple(record.quarantine_reasons),
            publish_blockers=tuple(record.publish_blockers),
        )

    @staticmethod
    def _to_audit(record: SkillAuditRecord) -> SkillAudit:
        return SkillAudit(
            action=record.action,
            skill_id=record.skill_id,
            version=record.version,
            actor=record.actor,
            created_at=record.created_at,
            visit_matter_id=record.visit_matter_id,
            turn_id=record.turn_id,
        )
