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


class VisitConversationStore(Protocol):
    async def ping(self) -> None: ...

    async def validate_visit_participant(
        self,
        visit_matter_id: str,
        participant_id: str,
    ) -> None: ...

    async def visit_stage(
        self, visit_matter_id: str, participant_id: str
    ) -> VisitStage: ...

    async def seed_development_visit_matter(
        self,
        visit_matter: DevelopmentVisitMatter,
    ) -> None: ...

    async def add_participant_message(
        self,
        *,
        turn: VisitTurn,
        content: str,
    ) -> StoredMessage: ...

    async def add_assistant_message(
        self,
        *,
        turn: VisitTurn,
    ) -> StoredMessage: ...

    async def mark_assistant_streaming(self, message_id: str) -> None: ...

    async def append_assistant_text(self, message_id: str, text: str) -> None: ...

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

    async def ping(self) -> None:
        return None

    async def validate_visit_participant(
        self,
        visit_matter_id: str,
        participant_id: str,
    ) -> None:
        async with self._lock:
            self._require_participant(visit_matter_id, participant_id)

    async def visit_stage(
        self, visit_matter_id: str, participant_id: str
    ) -> VisitStage:
        async with self._lock:
            self._require_participant(visit_matter_id, participant_id)
            return self._visit_matters[visit_matter_id].visit_stage

    async def seed_development_visit_matter(
        self,
        visit_matter: DevelopmentVisitMatter,
    ) -> None:
        async with self._lock:
            existing = self._visit_matters.get(visit_matter.visit_matter_id)
            if existing is not None and existing != visit_matter:
                raise IdempotencyConflictError("就诊事项 seed 与现有数据冲突")
            self._visit_matters[visit_matter.visit_matter_id] = visit_matter

    async def add_participant_message(
        self,
        *,
        turn: VisitTurn,
        content: str,
    ) -> StoredMessage:
        async with self._lock:
            self._require_participant(turn.visit_matter_id, turn.participant_id)
            key = (turn, "participant")
            existing = self._message_for_turn(key)
            if existing is not None:
                if existing.content != content:
                    raise IdempotencyConflictError("同一 turn 的参与者消息内容不一致")
                return existing
            return self._insert_message(
                turn=turn,
                role="participant",
                state="completed",
                content=content,
            )

    async def add_assistant_message(
        self,
        *,
        turn: VisitTurn,
    ) -> StoredMessage:
        async with self._lock:
            self._require_participant(turn.visit_matter_id, turn.participant_id)
            key = (turn, "assistant")
            existing = self._message_for_turn(key)
            if existing is not None:
                return existing
            return self._insert_message(
                turn=turn,
                role="assistant",
                state="pending",
                content="",
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
            sequence=self._next_sequence,
            created_at=now,
            updated_at=now,
        )
        self._next_sequence += 1
        self._messages[message.id] = message
        self._turn_messages[(turn, role)] = message.id
        return message

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
    ) -> None:
        self._messages[message.id] = replace(
            message,
            state=state if state is not None else message.state,
            content=content if content is not None else message.content,
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
