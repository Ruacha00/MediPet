from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import AsyncIterator, Sequence
from statistics import mean
from uuid import uuid4

from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from medipet.config import ModelSettings
from medipet.delivery.http import create_app
from medipet.model.openai import ChatOpenAIModelAdapter
from medipet.model.port import ModelChunk, ModelPort, ModelRequest
from medipet.persistence.conversation import DevelopmentVisitMatter
from medipet.persistence.postgres import PostgresVisitConversationStore
from medipet.run_metrics import InMemoryRunMetricStore, RunMetric

FICTIONAL_MESSAGE = "这是仅用于性能验证的虚构测试数据。"


class DeterministicBenchmarkModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelChunk]:
        del request
        yield ModelChunk(text="虚构", input_tokens=12)
        yield ModelChunk(text="测试响应", output_tokens=6)


def build_report(mode: str, metrics: Sequence[RunMetric]) -> dict[str, object]:
    if not metrics:
        raise ValueError("benchmark did not produce metrics")
    first_tokens = [item.first_token_ms for item in metrics if item.first_token_ms is not None]
    first = metrics[0]
    return {
        "mode": mode,
        "provider": first.provider,
        "model": first.model,
        "profileVersion": first.profile_version,
        "iterations": len(metrics),
        "average": {
            "firstTokenMs": mean(first_tokens) if first_tokens else None,
            "totalMs": mean(item.total_ms for item in metrics),
            "modelMs": mean(item.model_ms for item in metrics),
            "toolMs": mean(item.tool_ms for item in metrics),
            "modelRequests": mean(item.model_requests for item in metrics),
            "inputTokens": mean(item.input_tokens for item in metrics),
            "outputTokens": mean(item.output_tokens for item in metrics),
            "agentSteps": mean(item.agent_steps for item in metrics),
        },
        "outcomes": sorted({item.outcome for item in metrics}),
    }


async def run_benchmark(
    *,
    mode: str,
    database_url: str,
    model: ModelPort,
    provider: str,
    model_name: str,
    iterations: int,
) -> dict[str, object]:
    metrics = InMemoryRunMetricStore()
    store = PostgresVisitConversationStore.from_url(database_url)
    suffix = uuid4().hex
    visit = DevelopmentVisitMatter(
        patient_id=f"fictional-patient-{suffix}",
        patient_display_name="虚构测试患者",
        participant_id=f"fictional-participant-{suffix}",
        participant_display_name="虚构测试参与者",
        visit_matter_id=f"fictional-visit-{suffix}",
        visit_matter_title="虚构性能测试",
    )
    try:
        await store.seed_development_visit_matter(visit)
        app = create_app(
            model=model,
            model_provider=provider,
            model_name=model_name,
            conversation_store=store,
            run_metric_store=metrics,
            environment="test",
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://benchmark",
            timeout=120,
        ) as client:
            for index in range(iterations):
                response = await client.post(
                    "/v1/chat/turns",
                    json={
                        "visit_matter_id": visit.visit_matter_id,
                        "participant_id": visit.participant_id,
                        "idempotency_key": f"benchmark-{suffix}-{index}",
                        "messages": [
                            {
                                "id": f"message-{index}",
                                "role": "user",
                                "parts": [{"type": "text", "text": FICTIONAL_MESSAGE}],
                            }
                        ],
                    },
                )
                response.raise_for_status()
                if '"finish"' not in response.text:
                    raise RuntimeError("benchmark stream did not finish")
        return build_report(mode, await metrics.list_metrics())
    finally:
        await store.close()


def _upgrade_database(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a de-identified MediPet benchmark")
    parser.add_argument("mode", choices=("fake", "live"))
    parser.add_argument("--iterations", type=int, default=5)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.iterations <= 0:
        raise SystemExit("--iterations must be positive")
    database_url = os.getenv("MEDIPET_BENCHMARK_DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("MEDIPET_BENCHMARK_DATABASE_URL is required")
    if args.mode == "live" and os.getenv("MEDIPET_ENABLE_LIVE_BENCHMARK") != "1":
        raise SystemExit("Set MEDIPET_ENABLE_LIVE_BENCHMARK=1 to run a live model benchmark")

    if args.mode == "fake":
        model: ModelPort = DeterministicBenchmarkModel()
        provider = "fake"
        model_name = "deterministic-v1"
    else:
        settings = ModelSettings.from_environment(os.environ)
        model = ChatOpenAIModelAdapter(settings)
        provider = settings.provider
        model_name = settings.model

    _upgrade_database(database_url)
    report = asyncio.run(
        run_benchmark(
            mode=args.mode,
            database_url=database_url,
            model=model,
            provider=provider,
            model_name=model_name,
            iterations=args.iterations,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
