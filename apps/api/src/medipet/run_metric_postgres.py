from __future__ import annotations

from typing import cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from medipet.persistence.models import RunMetricRecord
from medipet.persistence.postgres import postgres_async_url
from medipet.run_metrics import RunMetric, TerminalOutcome


class PostgresRunMetricStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(cls, url: str) -> PostgresRunMetricStore:
        return cls(create_async_engine(postgres_async_url(url), pool_pre_ping=True))

    async def close(self) -> None:
        await self._engine.dispose()

    async def record_metric(self, metric: RunMetric) -> None:
        async with self._sessions.begin() as session:
            session.add(
                RunMetricRecord(
                    id=f"run-metric-{uuid4().hex}",
                    provider=metric.provider,
                    model=metric.model,
                    profile_version=metric.profile_version,
                    first_token_ms=metric.first_token_ms,
                    total_ms=metric.total_ms,
                    model_ms=metric.model_ms,
                    tool_ms=metric.tool_ms,
                    model_requests=metric.model_requests,
                    input_tokens=metric.input_tokens,
                    output_tokens=metric.output_tokens,
                    agent_steps=metric.agent_steps,
                    outcome=metric.outcome,
                    created_at=metric.created_at,
                )
            )

    async def list_metrics(self) -> list[RunMetric]:
        async with self._sessions() as session:
            records = await session.scalars(
                select(RunMetricRecord).order_by(
                    RunMetricRecord.created_at, RunMetricRecord.id
                )
            )
            return [
                RunMetric(
                    provider=record.provider,
                    model=record.model,
                    profile_version=record.profile_version,
                    first_token_ms=record.first_token_ms,
                    total_ms=record.total_ms,
                    model_ms=record.model_ms,
                    tool_ms=record.tool_ms,
                    model_requests=record.model_requests,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    agent_steps=record.agent_steps,
                    outcome=cast(TerminalOutcome, record.outcome),
                    created_at=record.created_at,
                )
                for record in records
            ]
