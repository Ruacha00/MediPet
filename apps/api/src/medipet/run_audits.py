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
class RunAudit:
    kind: RunAuditKind
    trace_id: str
    visit_matter_id: str
    turn_id: str
    profile_version: str
    created_at: datetime

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
    async def record(
        self,
        kind: RunAuditKind,
        *,
        trace_id: str,
        visit_matter_id: str,
        turn_id: str,
        profile_version: str,
    ) -> None: ...

    async def list_audits(self) -> list[RunAudit]: ...


class NullRunAuditStore:
    async def record(
        self,
        kind: RunAuditKind,
        *,
        trace_id: str,
        visit_matter_id: str,
        turn_id: str,
        profile_version: str,
    ) -> None:
        del kind, trace_id, visit_matter_id, turn_id, profile_version

    async def list_audits(self) -> list[RunAudit]:
        return []


class InMemoryRunAuditStore:
    def __init__(self) -> None:
        self._audits: list[RunAudit] = []
        self._lock = asyncio.Lock()

    async def record(
        self,
        kind: RunAuditKind,
        *,
        trace_id: str,
        visit_matter_id: str,
        turn_id: str,
        profile_version: str,
    ) -> None:
        async with self._lock:
            self._audits.append(
                RunAudit(
                    kind=kind,
                    trace_id=trace_id,
                    visit_matter_id=visit_matter_id,
                    turn_id=turn_id,
                    profile_version=profile_version,
                    created_at=datetime.now(UTC),
                )
            )

    async def list_audits(self) -> list[RunAudit]:
        async with self._lock:
            return list(self._audits)
