from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config

from medipet.run_metric_postgres import PostgresRunMetricStore
from medipet.run_metrics import RunMetric

DATABASE_URL = os.getenv("MEDIPET_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="MEDIPET_TEST_DATABASE_URL is required for PostgreSQL contract tests",
)


def alembic_config() -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", DATABASE_URL or "")
    return config


@pytest.mark.asyncio
async def test_postgres_run_metrics_survive_store_recreation() -> None:
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
    created_at = datetime.now(UTC)
    first = PostgresRunMetricStore.from_url(DATABASE_URL or "")
    try:
        await first.record_metric(
            RunMetric(
                provider="test-provider",
                model="test-model",
                profile_version="test-profile",
                first_token_ms=1.0,
                total_ms=3.0,
                model_ms=2.0,
                tool_ms=0.0,
                model_requests=1,
                input_tokens=4,
                output_tokens=5,
                agent_steps=1,
                outcome="completed",
                created_at=created_at,
            )
        )
    finally:
        await first.close()

    restarted = PostgresRunMetricStore.from_url(DATABASE_URL or "")
    try:
        metric = next(
            item
            for item in await restarted.list_metrics()
            if item.created_at == created_at
        )
        assert metric.provider == "test-provider"
        assert metric.model == "test-model"
        assert metric.profile_version == "test-profile"
        assert metric.input_tokens == 4
        assert metric.output_tokens == 5
    finally:
        await restarted.close()
