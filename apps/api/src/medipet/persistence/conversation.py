from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from medipet.agent.capabilities import VisitStage

MessageRole = Literal["participant", "assistant"]
MessageState = Literal["pending", "streaming", "completed", "failed", "cancelled"]
TerminalMessageState = Literal["completed", "failed", "cancelled"]
AssistantMessageTargetState = Literal["streaming", "completed", "failed", "cancelled"]

_ASSISTANT_TRANSITION_SOURCES: dict[
    AssistantMessageTargetState,
    tuple[MessageState, ...],
] = {
    "streaming": ("pending",),
    "completed": ("streaming",),
    "failed": ("pending", "streaming"),
    "cancelled": ("pending", "streaming"),
}


def assistant_transition_source_states(
    to_state: AssistantMessageTargetState,
) -> tuple[MessageState, ...]:
    return _ASSISTANT_TRANSITION_SOURCES[to_state]


class ConversationStoreError(RuntimeError):
    pass


class VisitMatterNotFoundError(ConversationStoreError):
    pass


class VisitMatterArchivedError(ConversationStoreError):
    pass


class VisitMatterBusyError(ConversationStoreError):
    pass


class MessageTransitionError(ConversationStoreError):
    pass


class IdempotencyConflictError(ConversationStoreError):
    pass


@dataclass(frozen=True)
class DevelopmentVisitMatter:
    patient_id: str
    patient_display_name: str
    participant_id: str
    participant_display_name: str
    visit_matter_id: str
    visit_matter_title: str
    visit_stage: VisitStage = "pre_visit"
    archived_at: datetime | None = None


@dataclass(frozen=True)
class VisitMatterSummary:
    visit_matter_id: str
    title: str
    visit_stage: VisitStage
    patient_display_name: str
    participant_display_name: str
    archived_at: datetime | None = None


@dataclass(frozen=True)
class VisitContext:
    patient_id: str
    patient_display_name: str
    visit_stage: VisitStage


@dataclass(frozen=True)
class VisitTurn:
    visit_matter_id: str
    participant_id: str
    turn_id: str


@dataclass(frozen=True)
class StoredMessage:
    id: str
    visit_matter_id: str
    participant_id: str
    turn_id: str
    role: MessageRole
    state: MessageState
    content: str
    sequence: int
    created_at: datetime
    updated_at: datetime
    parts: tuple[dict[str, object], ...] = ()
    selected_slot_id: str | None = None


class VisitConversationStore(Protocol):
    async def ping(self) -> None: ...

    async def validate_visit_participant(
        self,
        visit_matter_id: str,
        participant_id: str,
    ) -> None: ...

    async def visit_context(
        self,
        visit_matter_id: str,
        participant_id: str,
    ) -> VisitContext: ...

    async def visit_stage(self, visit_matter_id: str, participant_id: str) -> VisitStage: ...

    async def seed_development_visit_matter(
        self,
        visit_matter: DevelopmentVisitMatter,
    ) -> None: ...

    async def create_visit_matter(
        self,
        *,
        participant_id: str,
        title: str,
    ) -> VisitMatterSummary: ...

    async def list_visit_matters(
        self,
        participant_id: str,
        *,
        archived: bool = False,
    ) -> list[VisitMatterSummary]: ...

    async def rename_visit_matter(
        self,
        visit_matter_id: str,
        participant_id: str,
        *,
        title: str,
    ) -> VisitMatterSummary: ...

    async def archive_visit_matter(
        self,
        visit_matter_id: str,
        participant_id: str,
        *,
        pending_action: bool | None = None,
    ) -> VisitMatterSummary: ...

    async def restore_visit_matter(
        self,
        visit_matter_id: str,
        participant_id: str,
    ) -> VisitMatterSummary: ...

    async def add_participant_message(
        self,
        *,
        turn: VisitTurn,
        content: str,
        selected_slot_id: str | None = None,
    ) -> StoredMessage: ...

    async def add_assistant_message(
        self,
        *,
        turn: VisitTurn,
    ) -> StoredMessage: ...

    async def claim_assistant_message(
        self,
        *,
        turn: VisitTurn,
    ) -> tuple[StoredMessage, bool]: ...

    async def mark_assistant_streaming(self, message_id: str) -> None: ...

    async def append_assistant_text(self, message_id: str, text: str) -> None: ...

    async def append_assistant_part(self, message_id: str, part: dict[str, object]) -> None: ...

    async def update_action_proposal_part(
        self,
        visit_matter_id: str,
        proposal_id: str,
        part: dict[str, object],
    ) -> None: ...

    async def has_action_proposal_part(self, visit_matter_id: str, proposal_id: str) -> bool: ...

    async def finish_assistant_message(
        self,
        message_id: str,
        state: TerminalMessageState,
    ) -> None: ...

    async def list_messages(self, visit_matter_id: str) -> list[StoredMessage]: ...

    async def list_completed_messages(
        self,
        visit_matter_id: str,
        *,
        limit: int,
    ) -> list[StoredMessage]: ...


class InMemoryVisitConversationStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._visit_matters: dict[str, DevelopmentVisitMatter] = {}
        self._messages: dict[str, StoredMessage] = {}
        self._turn_messages: dict[tuple[VisitTurn, MessageRole], str] = {}
        self._next_sequence = 1
        self._visit_matter_activity: dict[str, int] = {}
        self._next_activity = 1

    async def ping(self) -> None:
        return None

    async def validate_visit_participant(
        self,
        visit_matter_id: str,
        participant_id: str,
    ) -> None:
        async with self._lock:
            self._require_participant(visit_matter_id, participant_id)

    async def visit_stage(self, visit_matter_id: str, participant_id: str) -> VisitStage:
        async with self._lock:
            self._require_participant(visit_matter_id, participant_id)
            return self._visit_matters[visit_matter_id].visit_stage

    async def visit_context(
        self,
        visit_matter_id: str,
        participant_id: str,
    ) -> VisitContext:
        async with self._lock:
            self._require_active_participant(visit_matter_id, participant_id)
            visit_matter = self._visit_matters[visit_matter_id]
            return VisitContext(
                patient_id=visit_matter.patient_id,
                patient_display_name=visit_matter.patient_display_name,
                visit_stage=visit_matter.visit_stage,
            )

    async def seed_development_visit_matter(
        self,
        visit_matter: DevelopmentVisitMatter,
    ) -> None:
        async with self._lock:
            existing = self._visit_matters.get(visit_matter.visit_matter_id)
            if existing is not None and existing != visit_matter:
                raise IdempotencyConflictError("就诊事项 seed 与现有数据冲突")
            if existing is None:
                self._touch_visit_matter(visit_matter.visit_matter_id)
            self._visit_matters[visit_matter.visit_matter_id] = visit_matter

    async def create_visit_matter(
        self,
        *,
        participant_id: str,
        title: str,
    ) -> VisitMatterSummary:
        async with self._lock:
            existing_visit_matter = next(
                (
                    visit_matter
                    for visit_matter in self._visit_matters.values()
                    if visit_matter.participant_id == participant_id
                ),
                None,
            )
            if existing_visit_matter is None:
                raise VisitMatterNotFoundError("就诊参与者不存在")
            visit_matter = DevelopmentVisitMatter(
                patient_id=existing_visit_matter.patient_id,
                patient_display_name=existing_visit_matter.patient_display_name,
                participant_id=existing_visit_matter.participant_id,
                participant_display_name=existing_visit_matter.participant_display_name,
                visit_matter_id=f"visit-matter-{uuid4().hex}",
                visit_matter_title=title,
            )
            self._visit_matters[visit_matter.visit_matter_id] = visit_matter
            self._touch_visit_matter(visit_matter.visit_matter_id)
            return self._summary(visit_matter)

    async def list_visit_matters(
        self,
        participant_id: str,
        *,
        archived: bool = False,
    ) -> list[VisitMatterSummary]:
        async with self._lock:
            visit_matters = sorted(
                (
                    visit_matter
                    for visit_matter in self._visit_matters.values()
                    if visit_matter.participant_id == participant_id
                    and (visit_matter.archived_at is not None) is archived
                ),
                key=lambda item: self._visit_matter_activity[item.visit_matter_id],
                reverse=True,
            )
            return [self._summary(visit_matter) for visit_matter in visit_matters]

    async def rename_visit_matter(
        self,
        visit_matter_id: str,
        participant_id: str,
        *,
        title: str,
    ) -> VisitMatterSummary:
        async with self._lock:
            self._require_participant(visit_matter_id, participant_id)
            visit_matter = replace(
                self._visit_matters[visit_matter_id],
                visit_matter_title=title,
            )
            self._visit_matters[visit_matter_id] = visit_matter
            return self._summary(visit_matter)

    async def archive_visit_matter(
        self,
        visit_matter_id: str,
        participant_id: str,
        *,
        pending_action: bool | None = None,
    ) -> VisitMatterSummary:
        async with self._lock:
            self._require_participant(visit_matter_id, participant_id)
            visit_matter = self._visit_matters[visit_matter_id]
            if visit_matter.archived_at is None:
                if self._visit_matter_is_busy(visit_matter_id, pending_action=pending_action):
                    raise VisitMatterBusyError("就诊事项仍有进行中的回复或待确认操作")
                visit_matter = replace(visit_matter, archived_at=datetime.now(UTC))
                self._visit_matters[visit_matter_id] = visit_matter
            return self._summary(visit_matter)

    async def restore_visit_matter(
        self,
        visit_matter_id: str,
        participant_id: str,
    ) -> VisitMatterSummary:
        async with self._lock:
            self._require_participant(visit_matter_id, participant_id)
            visit_matter = self._visit_matters[visit_matter_id]
            if visit_matter.archived_at is not None:
                visit_matter = replace(visit_matter, archived_at=None)
                self._visit_matters[visit_matter_id] = visit_matter
            return self._summary(visit_matter)

    async def add_participant_message(
        self,
        *,
        turn: VisitTurn,
        content: str,
        selected_slot_id: str | None = None,
    ) -> StoredMessage:
        async with self._lock:
            self._require_active_participant(turn.visit_matter_id, turn.participant_id)
            key = (turn, "participant")
            existing = self._message_for_turn(key)
            if existing is not None:
                if existing.content != content or existing.selected_slot_id != selected_slot_id:
                    raise IdempotencyConflictError("同一 turn 的参与者消息输入不一致")
                return existing
            return self._insert_message(
                turn=turn,
                role="participant",
                state="completed",
                content=content,
                selected_slot_id=selected_slot_id,
            )

    async def add_assistant_message(
        self,
        *,
        turn: VisitTurn,
    ) -> StoredMessage:
        message, _ = await self.claim_assistant_message(turn=turn)
        return message

    async def claim_assistant_message(
        self,
        *,
        turn: VisitTurn,
    ) -> tuple[StoredMessage, bool]:
        async with self._lock:
            self._require_active_participant(turn.visit_matter_id, turn.participant_id)
            key = (turn, "assistant")
            existing = self._message_for_turn(key)
            if existing is not None:
                return existing, False
            return (
                self._insert_message(
                    turn=turn,
                    role="assistant",
                    state="pending",
                    content="",
                ),
                True,
            )

    async def mark_assistant_streaming(self, message_id: str) -> None:
        async with self._lock:
            message = self._require_message(message_id)
            self._transition_assistant_message(message, "streaming")

    async def append_assistant_text(self, message_id: str, text: str) -> None:
        if not text:
            return
        async with self._lock:
            message = self._require_message(message_id)
            if message.role != "assistant" or message.state != "streaming":
                raise MessageTransitionError("只有 streaming 助手消息可以追加文本")
            self._replace_message(message, content=message.content + text)

    async def append_assistant_part(self, message_id: str, part: dict[str, object]) -> None:
        async with self._lock:
            message = self._require_message(message_id)
            if message.role != "assistant" or message.state not in {"pending", "streaming"}:
                raise MessageTransitionError("只有进行中的助手消息可以追加结构化内容")
            self._replace_message(message, parts=(*message.parts, dict(part)))

    async def update_action_proposal_part(
        self,
        visit_matter_id: str,
        proposal_id: str,
        part: dict[str, object],
    ) -> None:
        async with self._lock:
            for message in self._messages.values():
                if message.visit_matter_id != visit_matter_id or message.role != "assistant":
                    continue
                updated = replace_proposal_part(message.parts, proposal_id, part)
                if updated is not None:
                    self._replace_message(message, parts=updated)
                    return
            raise MessageTransitionError("聊天历史中不存在该预约确认")

    async def has_action_proposal_part(self, visit_matter_id: str, proposal_id: str) -> bool:
        async with self._lock:
            return any(
                message.visit_matter_id == visit_matter_id
                and message.role == "assistant"
                and replace_proposal_part(message.parts, proposal_id, {}) is not None
                for message in self._messages.values()
            )

    async def finish_assistant_message(
        self,
        message_id: str,
        state: TerminalMessageState,
    ) -> None:
        async with self._lock:
            message = self._require_message(message_id)
            self._transition_assistant_message(message, state)

    async def list_messages(self, visit_matter_id: str) -> list[StoredMessage]:
        async with self._lock:
            return [
                message
                for message in sorted(self._messages.values(), key=lambda item: item.sequence)
                if message.visit_matter_id == visit_matter_id
            ]

    async def list_completed_messages(
        self,
        visit_matter_id: str,
        *,
        limit: int,
    ) -> list[StoredMessage]:
        if limit <= 0:
            return []
        messages = await self.list_messages(visit_matter_id)
        return [message for message in messages if message.state == "completed"][-limit:]

    def _require_participant(self, visit_matter_id: str, participant_id: str) -> None:
        visit_matter = self._visit_matters.get(visit_matter_id)
        if visit_matter is None or visit_matter.participant_id != participant_id:
            raise VisitMatterNotFoundError("就诊事项不存在或参与者不匹配")

    def _require_active_participant(
        self,
        visit_matter_id: str,
        participant_id: str,
    ) -> None:
        self._require_participant(visit_matter_id, participant_id)
        if self._visit_matters[visit_matter_id].archived_at is not None:
            raise VisitMatterArchivedError("就诊事项已归档，请恢复后继续")

    def _visit_matter_is_busy(
        self,
        visit_matter_id: str,
        *,
        pending_action: bool | None = None,
    ) -> bool:
        visit_messages = [
            message
            for message in self._messages.values()
            if message.visit_matter_id == visit_matter_id
        ]
        participant_turns = {
            message.turn_id for message in visit_messages if message.role == "participant"
        }
        assistant_turns = {
            message.turn_id for message in visit_messages if message.role == "assistant"
        }
        return bool(participant_turns - assistant_turns) or any(
            (message.role == "assistant" and message.state in {"pending", "streaming"})
            or (pending_action is None and has_pending_action_proposal(message.parts))
            for message in visit_messages
        ) or pending_action is True

    def _message_for_turn(
        self,
        key: tuple[VisitTurn, MessageRole],
    ) -> StoredMessage | None:
        message_id = self._turn_messages.get(key)
        return self._messages.get(message_id) if message_id is not None else None

    def _insert_message(
        self,
        *,
        turn: VisitTurn,
        role: MessageRole,
        state: MessageState,
        content: str,
        selected_slot_id: str | None = None,
    ) -> StoredMessage:
        now = datetime.now(UTC)
        message = StoredMessage(
            id=f"message-{uuid4().hex}",
            visit_matter_id=turn.visit_matter_id,
            participant_id=turn.participant_id,
            turn_id=turn.turn_id,
            role=role,
            state=state,
            content=content,
            selected_slot_id=selected_slot_id,
            sequence=self._next_sequence,
            created_at=now,
            updated_at=now,
        )
        self._next_sequence += 1
        self._messages[message.id] = message
        self._turn_messages[(turn, role)] = message.id
        self._touch_visit_matter(turn.visit_matter_id)
        return message

    def _touch_visit_matter(self, visit_matter_id: str) -> None:
        self._visit_matter_activity[visit_matter_id] = self._next_activity
        self._next_activity += 1

    @staticmethod
    def _summary(visit_matter: DevelopmentVisitMatter) -> VisitMatterSummary:
        return VisitMatterSummary(
            visit_matter_id=visit_matter.visit_matter_id,
            title=visit_matter.visit_matter_title,
            visit_stage=visit_matter.visit_stage,
            patient_display_name=visit_matter.patient_display_name,
            participant_display_name=visit_matter.participant_display_name,
            archived_at=visit_matter.archived_at,
        )

    def _require_message(self, message_id: str) -> StoredMessage:
        try:
            return self._messages[message_id]
        except KeyError as error:
            raise MessageTransitionError("消息不存在") from error

    def _replace_message(
        self,
        message: StoredMessage,
        *,
        state: MessageState | None = None,
        content: str | None = None,
        parts: tuple[dict[str, object], ...] | None = None,
    ) -> None:
        self._messages[message.id] = replace(
            message,
            state=state if state is not None else message.state,
            content=content if content is not None else message.content,
            parts=parts if parts is not None else message.parts,
            updated_at=datetime.now(UTC),
        )

    def _transition_assistant_message(
        self,
        message: StoredMessage,
        to_state: AssistantMessageTargetState,
    ) -> None:
        if message.role != "assistant" or message.state not in assistant_transition_source_states(
            to_state
        ):
            raise MessageTransitionError("助手消息状态转换无效")
        self._replace_message(message, state=to_state)


def replace_proposal_part(
    parts: tuple[dict[str, object], ...],
    proposal_id: str,
    replacement: dict[str, object],
) -> tuple[dict[str, object], ...] | None:
    updated = list(parts)
    for index, existing in enumerate(updated):
        data = existing.get("data")
        if (
            existing.get("type") == "data-action-proposal"
            and isinstance(data, dict)
            and data.get("proposalId") == proposal_id
        ):
            updated[index] = dict(replacement)
            return tuple(updated)
    return None


def has_pending_action_proposal(parts: tuple[dict[str, object], ...]) -> bool:
    for part in parts:
        data = part.get("data")
        if (
            part.get("type") == "data-action-proposal"
            and isinstance(data, dict)
            and data.get("status") == "pending"
        ):
            return True
    return False
