from __future__ import annotations

from collections import defaultdict
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
from medipet.persistence.models import SkillAuditRecord, SkillRecord, SkillVersionRecord
from medipet.persistence.postgres import postgres_async_url
from medipet.skills.registry import (
    LifecycleAction,
    SkillAudit,
    SkillNotFoundError,
    SkillRegistryError,
    SkillStatus,
    SkillVersion,
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
                    self._to_version(skill, version).to_dict() for skill, version in pairs
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
                name=required_text(name, "Skill 名称不能为空"),
                description=required_text(description, "Skill 描述不能为空"),
                instructions=required_text(instructions, "Skill 指令不能为空"),
                change_note=required_text(change_note, "变更说明不能为空"),
                status="draft",
                active=False,
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
            record = SkillVersionRecord(
                id=f"skill-version-{uuid4().hex}",
                skill_id=skill_id,
                version=source.version + 1,
                name=source.name,
                description=source.description,
                instructions=required_text(instructions, "Skill 指令不能为空"),
                change_note=required_text(change_note, "变更说明不能为空"),
                status="draft",
                active=False,
            )
            session.add(record)
            await session.flush()
            self._add_audit(session, "edit", record, actor)
        return self._to_version(skill, record)

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
        session.add(
            SkillAuditRecord(
                id=f"skill-audit-{uuid4().hex}",
                action=action,
                skill_id=record.skill_id,
                version=record.version,
                actor=actor,
            )
        )

    @staticmethod
    def _to_version(skill: SkillRecord, record: SkillVersionRecord) -> SkillVersion:
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
