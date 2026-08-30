from __future__ import annotations

from collections import defaultdict
from typing import cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from medipet.agent.capabilities import (
    SkillDefinition,
    ToolContext,
    ToolDefinition,
    VisitStage,
)
from medipet.persistence.models import (
    SkillVersionRecord,
    ToolAuditRecord,
    ToolBindingRecord,
    ToolRecord,
    ToolVersionRecord,
)
from medipet.persistence.postgres import postgres_async_url
from medipet.tools.registry import (
    ToolAudit,
    ToolEffect,
    ToolNotFoundError,
    ToolRegistryError,
    ToolVersion,
    TrustedTool,
    _validate_provider_tools,
)


class _SyncRejected(Exception):
    def __init__(self, message: str, tool_id: str, version: str) -> None:
        super().__init__(message)
        self.tool_id = tool_id
        self.version = version


class PostgresToolRegistry:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)
        self._implementations: dict[tuple[str, str], TrustedTool] = {}

    @classmethod
    def from_url(cls, url: str) -> PostgresToolRegistry:
        return cls(create_async_engine(postgres_async_url(url), pool_pre_ping=True))

    async def close(self) -> None:
        await self._engine.dispose()

    async def synchronize(self, tools: tuple[TrustedTool, ...], *, actor: str) -> None:
        try:
            _validate_provider_tools(tools)
        except ToolRegistryError as error:
            rejected = tools[0] if tools else None
            await self._record_audit(
                "reject_sync",
                actor,
                rejected.tool_id if rejected else "",
                rejected.version if rejected else "",
            )
            raise error
        supplied: set[tuple[str, str]] = set()
        try:
            async with self._sessions.begin() as session:
                existing = (await session.scalars(select(ToolVersionRecord))).all()
                by_key = {(item.tool_id, item.version): item for item in existing}
                for tool in tools:
                    key = (tool.tool_id, tool.version)
                    supplied.add(key)
                    record = by_key.get(key)
                    if record is not None:
                        if self._contract(record) != tool.contract():
                            raise _SyncRejected(
                                "Tool contract changed under an existing version; "
                                "deploy a new version",
                                *key,
                            )
                        record.available = True
                        self._implementations[key] = tool
                        continue
                    if not await session.get(ToolRecord, tool.tool_id):
                        session.add(ToolRecord(id=tool.tool_id))
                        await session.flush()
                    has_versions = any(item.tool_id == tool.tool_id for item in existing)
                    session.add(
                        ToolVersionRecord(
                            id=f"tool-version-{uuid4().hex}",
                            tool_id=tool.tool_id,
                            version=tool.version,
                            name=tool.name,
                            description=tool.description,
                            input_schema=tool.input_schema,
                            output_schema=tool.output_schema,
                            effect=tool.effect,
                            allowed_stages=list(tool.allowed_stages),
                            enabled=False,
                            available=True,
                            approval_required=tool.approval_required,
                            provider_approval_required=tool.approval_required,
                        )
                    )
                    self._implementations[key] = tool
                    self._add_audit(
                        session, "version" if has_versions else "sync", actor, *key
                    )
                for record in existing:
                    key = (record.tool_id, record.version)
                    if record.available and key not in supplied:
                        record.available = False
                        self._implementations.pop(key, None)
                        self._add_audit(session, "missing", actor, *key)
        except _SyncRejected as error:
            await self._record_audit(
                "reject_sync", actor, error.tool_id, error.version
            )
            raise ToolRegistryError(str(error)) from error

    async def list_tools(self) -> list[dict[str, object]]:
        async with self._sessions() as session:
            records = (
                await session.scalars(
                    select(ToolVersionRecord).order_by(
                        ToolVersionRecord.tool_id, ToolVersionRecord.created_at
                    )
                )
            ).all()
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for record in records:
            grouped[record.tool_id].append(self._record_dict(record))
        return [{"tool_id": tool_id, "versions": versions} for tool_id, versions in grouped.items()]

    async def configure(
        self, tool_id: str, version: str, *, enabled: bool, approval_required: bool, actor: str
    ) -> ToolVersion:
        rejection: tuple[str, str] | None = None
        async with self._sessions.begin() as session:
            record = await self._require(session, tool_id, version)
            if record.effect == "write" and not approval_required:
                rejection = ("reject_configure", "Write Tool approval cannot be disabled")
            elif enabled and not record.available:
                rejection = ("reject_enable", "Missing Tool implementation cannot be enabled")
            else:
                approval_changed = record.approval_required != approval_required
                record.enabled = enabled
                record.approval_required = approval_required
                self._add_audit(
                    session, "enable" if enabled else "disable", actor, tool_id, version
                )
                if approval_changed:
                    self._add_audit(session, "configure_approval", actor, tool_id, version)
        if rejection is not None:
            await self._record_audit(rejection[0], actor, tool_id, version)
            raise ToolRegistryError(rejection[1])
        return self._to_version(record)

    async def bind(
        self, skill_id: str, skill_version: int, tool_id: str, tool_version: str, *, actor: str
    ) -> dict[str, object]:
        rejection: str | None = None
        async with self._sessions.begin() as session:
            skill = await session.scalar(
                select(SkillVersionRecord).where(
                    SkillVersionRecord.skill_id == skill_id,
                    SkillVersionRecord.version == skill_version,
                )
            )
            if skill is None:
                rejection = "Skill version does not exist"
            elif skill.status in {"published", "retired"}:
                rejection = "Published Skill Tool bindings are immutable"
            if rejection is None:
                await self._require(session, tool_id, tool_version)
                existing = await session.scalar(
                    select(ToolBindingRecord).where(
                        ToolBindingRecord.skill_id == skill_id,
                        ToolBindingRecord.skill_version == skill_version,
                        ToolBindingRecord.tool_id == tool_id,
                        ToolBindingRecord.tool_version == tool_version,
                    )
                )
                if existing is None:
                    session.add(
                        ToolBindingRecord(
                            id=f"tool-binding-{uuid4().hex}",
                            skill_id=skill_id,
                            skill_version=skill_version,
                            tool_id=tool_id,
                            tool_version=tool_version,
                        )
                    )
                    self._add_audit(session, "bind", actor, tool_id, tool_version)
        if rejection is not None:
            await self._record_audit("reject_bind", actor, tool_id, tool_version)
            raise ToolRegistryError(rejection)
        return {
            "skill_id": skill_id,
            "skill_version": skill_version,
            "tool_id": tool_id,
            "tool_version": tool_version,
        }

    async def freeze_bindings(self, skill_id: str, skill_version: int) -> None:
        del skill_id, skill_version

    async def validate_bindings(self, skill_id: str, skill_version: int) -> None:
        async with self._sessions() as session:
            bindings = (
                await session.scalars(
                    select(ToolBindingRecord).where(
                        ToolBindingRecord.skill_id == skill_id,
                        ToolBindingRecord.skill_version == skill_version,
                    )
                )
            ).all()
            if not bindings:
                raise ToolRegistryError("tool-assisted Skill requires a Tool binding")
            for binding in bindings:
                record = await self._require(session, binding.tool_id, binding.tool_version)
                if not record.available or not record.enabled:
                    raise ToolRegistryError(
                        "tool-assisted Skill binding must reference an enabled "
                        "compatible Tool version"
                    )

    async def runtime_tools(
        self, skills: tuple[SkillDefinition, ...], context: ToolContext
    ) -> tuple[ToolDefinition, ...]:
        definitions: list[ToolDefinition] = []
        async with self._sessions() as session:
            for skill in skills:
                bindings = (
                    await session.scalars(
                        select(ToolBindingRecord).where(
                            ToolBindingRecord.skill_id == skill.skill_id,
                            ToolBindingRecord.skill_version == skill.version,
                        )
                    )
                ).all()
                for binding in bindings:
                    record = await self._require(session, binding.tool_id, binding.tool_version)
                    implementation = self._implementations.get((record.tool_id, record.version))
                    if implementation is None:
                        continue

                    async def execute(
                        arguments: dict[str, object],
                        execution_context: ToolContext,
                        pinned: TrustedTool = implementation,
                    ) -> dict[str, object]:
                        try:
                            result = await pinned.execute(arguments, execution_context)
                        except Exception:
                            await self.record_invocation(
                                "reject_invoke",
                                pinned.tool_id,
                                pinned.version,
                                execution_context,
                            )
                            raise
                        await self.record_invocation(
                            "invoke", pinned.tool_id, pinned.version, execution_context
                        )
                        return result

                    async def reject(
                        rejection_context: ToolContext, pinned: TrustedTool = implementation
                    ) -> None:
                        await self.record_invocation(
                            "reject_invoke", pinned.tool_id, pinned.version, rejection_context
                        )

                    async def revalidate(
                        validation_context: ToolContext,
                        pinned: TrustedTool = implementation,
                    ) -> bool:
                        async with self._sessions() as validation_session:
                            current = await self._require(
                                validation_session, pinned.tool_id, pinned.version
                            )
                        return (
                            current.enabled
                            and current.available
                            and validation_context.visit_stage in pinned.allowed_stages
                            and pinned.authorize(validation_context)
                        )

                    definitions.append(
                        ToolDefinition(
                            name=record.name,
                            version=record.version,
                            description=record.description,
                            input_schema=record.input_schema,
                            output_schema=record.output_schema,
                            effect=cast(ToolEffect, record.effect),
                            execute=execute,
                            tool_id=record.tool_id,
                            approval_required=record.approval_required,
                            enabled=record.enabled
                            and record.available
                            and context.visit_stage in record.allowed_stages
                            and implementation.authorize(context),
                            authorize=implementation.authorize,
                            record_rejection=reject,
                            revalidate=revalidate,
                        )
                    )
        return tuple(definitions)

    async def record_invocation(
        self, action: str, tool_id: str, version: str, context: ToolContext
    ) -> None:
        async with self._sessions.begin() as session:
            self._add_audit(
                session,
                action,
                "runtime",
                tool_id,
                version,
                visit_matter_id=context.visit_matter_id,
                turn_id=context.idempotency_key,
            )

    async def record_unknown_rejection(
        self, tool_name: str, context: ToolContext
    ) -> None:
        async with self._sessions.begin() as session:
            self._add_audit(
                session,
                "reject_invoke",
                "runtime",
                tool_name,
                None,
                visit_matter_id=context.visit_matter_id,
                turn_id=context.idempotency_key,
            )

    async def _record_audit(
        self, action: str, actor: str, tool_id: str, version: str
    ) -> None:
        async with self._sessions.begin() as session:
            self._add_audit(session, action, actor, tool_id, version)

    async def list_audits(self) -> list[ToolAudit]:
        async with self._sessions() as session:
            records = (
                await session.scalars(select(ToolAuditRecord).order_by(ToolAuditRecord.created_at))
            ).all()
        return [
            ToolAudit(
                action=item.action,
                actor=item.actor,
                created_at=item.created_at,
                tool_id=item.tool_id,
                version=item.version,
                visit_matter_id=item.visit_matter_id,
                turn_id=item.turn_id,
            )
            for item in records
        ]

    async def _require(self, session, tool_id: str, version: str) -> ToolVersionRecord:
        record = await session.scalar(
            select(ToolVersionRecord).where(
                ToolVersionRecord.tool_id == tool_id, ToolVersionRecord.version == version
            )
        )
        if record is None:
            raise ToolNotFoundError("Tool version does not exist")
        return record

    @staticmethod
    def _add_audit(
        session,
        action: str,
        actor: str,
        tool_id: str,
        version: str | None,
        *,
        visit_matter_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        session.add(
            ToolAuditRecord(
                id=f"tool-audit-{uuid4().hex}",
                action=action,
                actor=actor,
                tool_id=tool_id,
                version=version,
                visit_matter_id=visit_matter_id or None,
                turn_id=turn_id or None,
            )
        )

    def _to_version(self, record: ToolVersionRecord) -> ToolVersion:
        implementation = self._implementations.get((record.tool_id, record.version))
        if implementation is None:

            async def unavailable(arguments, context):
                del arguments, context
                raise ToolRegistryError("Tool implementation is unavailable")

            implementation = TrustedTool(
                tool_id=record.tool_id,
                version=record.version,
                name=record.name,
                description=record.description,
                input_schema=record.input_schema,
                output_schema=record.output_schema,
                effect=cast(ToolEffect, record.effect),
                approval_required=record.approval_required,
                execute=unavailable,
                allowed_stages=tuple(cast(list[VisitStage], record.allowed_stages)),
            )
        return ToolVersion(
            tool=implementation,
            enabled=record.enabled,
            available=record.available,
            approval_required=record.approval_required,
        )

    @staticmethod
    def _contract(record: ToolVersionRecord) -> dict[str, object]:
        return {
            "tool_id": record.tool_id,
            "version": record.version,
            "name": record.name,
            "description": record.description,
            "input_schema": record.input_schema,
            "output_schema": record.output_schema,
            "effect": record.effect,
            "allowed_stages": record.allowed_stages,
            "provider_approval_required": record.provider_approval_required,
        }

    @staticmethod
    def _record_dict(record: ToolVersionRecord) -> dict[str, object]:
        return {
            **PostgresToolRegistry._contract(record),
            "enabled": record.enabled,
            "available": record.available,
            "approval_required": record.approval_required,
        }
