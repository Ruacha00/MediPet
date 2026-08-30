from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal, Protocol

TerminalOutcome = Literal[
    "completed",
    "failed",
    "cancelled",
    "turn_timeout",
]


@dataclass(frozen=True)
class RunMetric:
    provider: str
    model: str
    profile_version: str
    first_token_ms: float | None
    total_ms: float
    model_ms: float
    tool_ms: float
    model_requests: int
    input_tokens: int
    output_tokens: int
    agent_steps: int
    outcome: TerminalOutcome
    created_at: datetime

    def to_dict(self) -> dict[str, str | float | int | None]:
        return {
            "provider": self.provider,
            "model": self.model,
            "profileVersion": self.profile_version,
            "firstTokenMs": self.first_token_ms,
            "totalMs": self.total_ms,
            "modelMs": self.model_ms,
            "toolMs": self.tool_ms,
            "modelRequests": self.model_requests,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "agentSteps": self.agent_steps,
            "outcome": self.outcome,
            "createdAt": self.created_at.isoformat(),
        }


class RunMetricStore(Protocol):
    async def record_metric(self, metric: RunMetric) -> None: ...

    async def list_metrics(self) -> list[RunMetric]: ...


class NullRunMetricStore:
    async def record_metric(self, metric: RunMetric) -> None:
        del metric

    async def list_metrics(self) -> list[RunMetric]:
        return []


class InMemoryRunMetricStore:
    def __init__(self) -> None:
        self._metrics: list[RunMetric] = []
        self._lock = asyncio.Lock()

    async def record_metric(self, metric: RunMetric) -> None:
        async with self._lock:
            self._metrics.append(metric)

    async def list_metrics(self) -> list[RunMetric]:
        async with self._lock:
            return list(self._metrics)


class RunMetricsRecorder:
    """Collects numeric run telemetry without retaining messages, prompts, or URLs."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        profile_version: str,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._provider = provider
        self._model = model
        self._profile_version = profile_version
        self._clock = clock
        self._started = clock()
        self._first_token_at: float | None = None
        self._model_ms = 0.0
        self._tool_ms = 0.0
        self._model_requests = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._agent_steps = 0

    def begin_model_call(self) -> float:
        self._agent_steps += 1
        self._model_requests += 1
        return self._clock()

    def end_model_call(
        self,
        started: float,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self._model_ms += max(0.0, (self._clock() - started) * 1000)
        self._input_tokens += max(0, input_tokens)
        self._output_tokens += max(0, output_tokens)

    def begin_tool_call(self) -> float:
        self._agent_steps += 1
        return self._clock()

    def end_tool_call(self, started: float) -> None:
        self._tool_ms += max(0.0, (self._clock() - started) * 1000)

    def mark_first_token(self) -> None:
        if self._first_token_at is None:
            self._first_token_at = self._clock()

    def finish(self, outcome: TerminalOutcome) -> RunMetric:
        finished = self._clock()
        return RunMetric(
            provider=self._provider,
            model=self._model,
            profile_version=self._profile_version,
            first_token_ms=(
                None
                if self._first_token_at is None
                else max(0.0, (self._first_token_at - self._started) * 1000)
            ),
            total_ms=max(0.0, (finished - self._started) * 1000),
            model_ms=self._model_ms,
            tool_ms=self._tool_ms,
            model_requests=self._model_requests,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            agent_steps=self._agent_steps,
            outcome=outcome,
            created_at=datetime.now(UTC),
        )
