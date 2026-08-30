from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class ToolContext:
    visit_matter_id: str = ""
    participant_id: str = ""
    turn_id: str = ""
    profile_version: str = "static"


ToolExecutor = Callable[[dict[str, object], ToolContext], Awaitable[dict[str, object]]]
ToolAuthorizer = Callable[[ToolContext], bool]


def _allow(_: ToolContext) -> bool:
    return True


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    input_schema: Mapping[str, object]
    effect: Literal["read", "write"]
    execute: ToolExecutor
    enabled: bool = True
    bound: bool = True
    authorize: ToolAuthorizer = _allow


@dataclass(frozen=True)
class CapabilitySnapshot:
    skill_versions: tuple[str, ...] = ()
    tools: tuple[ToolDefinition, ...] = ()

    @classmethod
    def empty(cls) -> CapabilitySnapshot:
        return cls()


class CapabilityProvider(Protocol):
    async def snapshot(self, context: ToolContext) -> CapabilitySnapshot: ...


class StaticCapabilityProvider:
    """Dependency-injected provider used for empty production and test capabilities."""

    def __init__(self, snapshot: CapabilitySnapshot | None = None) -> None:
        self._snapshot = snapshot or CapabilitySnapshot.empty()

    async def snapshot(self, context: ToolContext) -> CapabilitySnapshot:
        del context
        return self._snapshot

    def replace(self, snapshot: CapabilitySnapshot) -> None:
        self._snapshot = snapshot
