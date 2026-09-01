from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

VisitStage = Literal["pre_visit", "in_visit"]


@dataclass(frozen=True)
class ParticipantToolSelection:
    tool_name: str
    arguments: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ToolContext:
    visit_matter_id: str = ""
    participant_id: str = ""
    idempotency_key: str = ""
    profile_version: str = "static"
    visit_stage: VisitStage = "pre_visit"
    patient_id: str = ""
    patient_display_name: str = ""
    participant_tool_selection: ParticipantToolSelection | None = None


ToolExecutor = Callable[[dict[str, object], ToolContext], Awaitable[dict[str, object]]]
ToolConfirmationPreparer = Callable[
    [dict[str, object], ToolContext], Awaitable[dict[str, object]]
]
ToolConfirmationRevalidator = Callable[
    [dict[str, object], dict[str, object], ToolContext], Awaitable[bool]
]
ToolPresenter = Callable[
    [dict[str, object], ToolContext], Awaitable[tuple[dict[str, object], ...]]
]
ToolAuthorizer = Callable[[ToolContext], bool]
ToolRejectionRecorder = Callable[[ToolContext], Awaitable[None]]
ToolAvailabilityValidator = Callable[[ToolContext], Awaitable[bool]]
UnknownToolRejectionRecorder = Callable[[str, ToolContext], Awaitable[None]]
SkillInstructionLoader = Callable[[], Awaitable[str]]


def _allow(_: ToolContext) -> bool:
    return True


@dataclass(frozen=True)
class ToolConfirmationContract:
    schema: Mapping[str, object]
    prepare: ToolConfirmationPreparer
    revalidate: ToolConfirmationRevalidator


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    input_schema: Mapping[str, object]
    effect: Literal["read", "write"]
    execute: ToolExecutor
    tool_id: str = ""
    output_schema: Mapping[str, object] | None = None
    confirmation_contract: ToolConfirmationContract | None = None
    approval_required: bool = False
    enabled: bool = True
    bound: bool = True
    authorize: ToolAuthorizer = _allow
    record_rejection: ToolRejectionRecorder | None = None
    revalidate: ToolAvailabilityValidator | None = None
    present: ToolPresenter | None = None
    required_skill_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillDefinition:
    skill_id: str
    slug: str
    version: int
    name: str
    description: str
    load_instructions: SkillInstructionLoader


@dataclass(frozen=True)
class CapabilitySnapshot:
    skill_versions: tuple[str, ...] = ()
    skills: tuple[SkillDefinition, ...] = ()
    tools: tuple[ToolDefinition, ...] = ()
    record_unknown_tool_rejection: UnknownToolRejectionRecorder | None = None

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
