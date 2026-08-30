from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

RunAuditKind = Literal[
    "retry",
    "model_timeout",
    "turn_timeout",
    "loop_detected",
    "budget_exhausted",
    "completed",
    "failed",
    "cancelled",
]


@dataclass(frozen=True)
class RunAuditContext:
    trace_id: str
    visit_matter_id: str
    turn_id: str
    profile_version: str


@dataclass(frozen=True)
class RunAudit:
    kind: RunAuditKind
    context: RunAuditContext
    created_at: datetime

    @property
    def trace_id(self) -> str:
        return self.context.trace_id

    @property
    def visit_matter_id(self) -> str:
        return self.context.visit_matter_id

    @property
    def turn_id(self) -> str:
        return self.context.turn_id

    @property
    def profile_version(self) -> str:
        return self.context.profile_version

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "traceId": self.trace_id,
            "visitMatterId": self.visit_matter_id,
            "turnId": self.turn_id,
            "profileVersion": self.profile_version,
            "createdAt": self.created_at.isoformat(),
        }


class RunAuditStore(Protocol):
    async def record(self, kind: RunAuditKind, context: RunAuditContext) -> None: ...

    async def list_audits(self) -> list[RunAudit]: ...


class NullRunAuditStore:
    async def record(self, kind: RunAuditKind, context: RunAuditContext) -> None:
        del kind, context

    async def list_audits(self) -> list[RunAudit]:
        return []


class InMemoryRunAuditStore:
    def __init__(self) -> None:
        self._audits: list[RunAudit] = []
        self._lock = asyncio.Lock()

    async def record(self, kind: RunAuditKind, context: RunAuditContext) -> None:
        async with self._lock:
            self._audits.append(
                RunAudit(
                    kind=kind,
                    context=context,
                    created_at=datetime.now(UTC),
                )
            )

    async def list_audits(self) -> list[RunAudit]:
        async with self._lock:
            return list(self._audits)
