from __future__ import annotations

from typing import cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from medipet.persistence.models import RunAuditRecord
from medipet.persistence.postgres import postgres_async_url
from medipet.run_audits import RunAudit, RunAuditContext, RunAuditKind


class PostgresRunAuditStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(cls, url: str) -> PostgresRunAuditStore:
        return cls(create_async_engine(postgres_async_url(url), pool_pre_ping=True))

    async def close(self) -> None:
        await self._engine.dispose()

    async def record(self, kind: RunAuditKind, context: RunAuditContext) -> None:
        async with self._sessions.begin() as session:
            session.add(
                RunAuditRecord(
                    id=f"run-audit-{uuid4().hex}",
                    kind=kind,
                    trace_id=context.trace_id,
                    visit_matter_id=context.visit_matter_id,
                    turn_id=context.turn_id,
                    profile_version=context.profile_version,
                )
            )

    async def list_audits(self) -> list[RunAudit]:
        async with self._sessions() as session:
            records = await session.scalars(
                select(RunAuditRecord).order_by(RunAuditRecord.created_at, RunAuditRecord.id)
            )
            return [
                RunAudit(
                    kind=cast(RunAuditKind, record.kind),
                    context=RunAuditContext(
                        trace_id=record.trace_id,
                        visit_matter_id=record.visit_matter_id,
                        turn_id=record.turn_id,
                        profile_version=record.profile_version,
                    ),
                    created_at=record.created_at,
                )
                for record in records
            ]
