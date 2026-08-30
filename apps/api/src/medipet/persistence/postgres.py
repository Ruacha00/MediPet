from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from medipet.persistence.conversation import (
    DevelopmentVisit,
    IdempotencyConflictError,
    MessageRole,
    MessageState,
    MessageTransitionError,
    StoredMessage,
    TerminalMessageState,
    VisitMatterNotFoundError,
)
from medipet.persistence.models import (
    ConversationMessageRecord,
    PatientRecord,
    VisitMatterRecord,
    VisitParticipantRecord,
)


class DatabaseConfigurationError(ValueError):
    pass


def postgres_async_url(url: str) -> str:
    value = url.strip()
    if value.startswith("postgresql+asyncpg://"):
        return value
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+asyncpg://", 1)
    raise DatabaseConfigurationError("数据库地址必须使用 PostgreSQL")


class PostgresVisitConversationStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(cls, url: str) -> PostgresVisitConversationStore:
        return cls(create_async_engine(postgres_async_url(url), pool_pre_ping=True))

    async def close(self) -> None:
        await self._engine.dispose()

    async def ping(self) -> None:
        async with self._sessions() as session:
            await session.execute(text("SELECT 1"))

    async def validate_visit_participant(
        self,
        visit_matter_id: str,
        participant_id: str,
    ) -> None:
        async with self._sessions() as session:
            await self._require_visit(session, visit_matter_id, participant_id)

    async def seed_development_visit(self, visit: DevelopmentVisit) -> None:
        async with self._sessions.begin() as session:
            await self._seed_record(
                session,
                PatientRecord,
                visit.patient_id,
                {"display_name": visit.patient_display_name},
            )
            await self._seed_record(
                session,
                VisitParticipantRecord,
                visit.participant_id,
                {
                    "patient_id": visit.patient_id,
                    "display_name": visit.participant_display_name,
                },
            )
            await self._seed_record(
                session,
                VisitMatterRecord,
                visit.visit_matter_id,
                {
                    "patient_id": visit.patient_id,
                    "participant_id": visit.participant_id,
                    "title": visit.visit_matter_title,
                },
            )

    async def add_participant_message(
        self,
        *,
        visit_matter_id: str,
        participant_id: str,
        turn_id: str,
        content: str,
    ) -> StoredMessage:
        return await self._add_message(
            visit_matter_id=visit_matter_id,
            participant_id=participant_id,
            turn_id=turn_id,
            role="user",
            state="completed",
            content=content,
        )

    async def add_assistant_message(
        self,
        *,
        visit_matter_id: str,
        participant_id: str,
        turn_id: str,
    ) -> StoredMessage:
        return await self._add_message(
            visit_matter_id=visit_matter_id,
            participant_id=participant_id,
            turn_id=turn_id,
            role="assistant",
            state="pending",
            content="",
        )

    async def mark_assistant_streaming(self, message_id: str) -> None:
        await self._transition(
            message_id,
            from_states=("pending",),
            to_state="streaming",
        )

    async def append_assistant_text(self, message_id: str, text: str) -> None:
        if not text:
            return
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(ConversationMessageRecord)
                .where(
                    ConversationMessageRecord.id == message_id,
                    ConversationMessageRecord.role == "assistant",
                    ConversationMessageRecord.state == "streaming",
                )
                .values(
                    content=ConversationMessageRecord.content + text,
                    updated_at=func.now(),
                )
                .returning(ConversationMessageRecord.id)
            )
            if result.scalar_one_or_none() is None:
                raise MessageTransitionError("只有 streaming 助手消息可以追加文本")

    async def finish_assistant_message(
        self,
        message_id: str,
        state: TerminalMessageState,
    ) -> None:
        from_states = ("streaming",) if state == "completed" else ("pending", "streaming")
        await self._transition(message_id, from_states=from_states, to_state=state)

    async def list_messages(self, visit_matter_id: str) -> list[StoredMessage]:
        async with self._sessions() as session:
            records = (
                await session.scalars(
                    select(ConversationMessageRecord)
                    .where(ConversationMessageRecord.visit_matter_id == visit_matter_id)
                    .order_by(ConversationMessageRecord.sequence)
                )
            ).all()
            return [self._to_message(record) for record in records]

    async def list_completed_messages(
        self,
        visit_matter_id: str,
        *,
        limit: int,
    ) -> list[StoredMessage]:
        if limit <= 0:
            return []
        async with self._sessions() as session:
            recent = (
                select(ConversationMessageRecord)
                .where(
                    ConversationMessageRecord.visit_matter_id == visit_matter_id,
                    ConversationMessageRecord.state == "completed",
                )
                .order_by(ConversationMessageRecord.sequence.desc())
                .limit(limit)
                .subquery()
            )
            records = (
                await session.scalars(
                    select(ConversationMessageRecord)
                    .join(recent, ConversationMessageRecord.id == recent.c.id)
                    .order_by(ConversationMessageRecord.sequence)
                )
            ).all()
            return [self._to_message(record) for record in records]

    async def _add_message(
        self,
        *,
        visit_matter_id: str,
        participant_id: str,
        turn_id: str,
        role: MessageRole,
        state: MessageState,
        content: str,
    ) -> StoredMessage:
        async with self._sessions.begin() as session:
            await self._require_visit(session, visit_matter_id, participant_id)
            message_id = f"message-{uuid4().hex}"
            statement = (
                insert(ConversationMessageRecord)
                .values(
                    id=message_id,
                    visit_matter_id=visit_matter_id,
                    participant_id=participant_id,
                    turn_id=turn_id,
                    role=role,
                    state=state,
                    content=content,
                )
                .on_conflict_do_nothing(index_elements=("visit_matter_id", "turn_id", "role"))
                .returning(ConversationMessageRecord.id)
            )
            inserted_id = (await session.execute(statement)).scalar_one_or_none()
            record = await session.scalar(
                select(ConversationMessageRecord).where(
                    ConversationMessageRecord.visit_matter_id == visit_matter_id,
                    ConversationMessageRecord.turn_id == turn_id,
                    ConversationMessageRecord.role == role,
                )
            )
            if record is None:
                raise RuntimeError("消息写入后无法读取")
            if inserted_id is None and role == "user" and record.content != content:
                raise IdempotencyConflictError("同一 turn 的参与者消息内容不一致")
            if inserted_id is not None:
                await session.execute(
                    update(VisitMatterRecord)
                    .where(VisitMatterRecord.id == visit_matter_id)
                    .values(
                        latest_message_sequence=func.greatest(
                            func.coalesce(VisitMatterRecord.latest_message_sequence, 0),
                            record.sequence,
                        ),
                        updated_at=func.now(),
                    )
                )
            return self._to_message(record)

    async def _transition(
        self,
        message_id: str,
        *,
        from_states: tuple[MessageState, ...],
        to_state: MessageState,
    ) -> None:
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(ConversationMessageRecord)
                .where(
                    ConversationMessageRecord.id == message_id,
                    ConversationMessageRecord.role == "assistant",
                    ConversationMessageRecord.state.in_(from_states),
                )
                .values(state=to_state, updated_at=func.now())
                .returning(ConversationMessageRecord.id)
            )
            if result.scalar_one_or_none() is None:
                raise MessageTransitionError("助手消息状态转换无效")

    @staticmethod
    async def _require_visit(
        session: AsyncSession,
        visit_matter_id: str,
        participant_id: str,
    ) -> None:
        exists = await session.scalar(
            select(VisitMatterRecord.id).where(
                VisitMatterRecord.id == visit_matter_id,
                VisitMatterRecord.participant_id == participant_id,
            )
        )
        if exists is None:
            raise VisitMatterNotFoundError("就诊事项不存在或参与者不匹配")

    @staticmethod
    async def _seed_record(
        session: AsyncSession,
        record_type: type[PatientRecord] | type[VisitParticipantRecord] | type[VisitMatterRecord],
        record_id: str,
        values: Mapping[str, str],
    ) -> None:
        record = await session.get(record_type, record_id)
        if record is None:
            session.add(record_type(id=record_id, **values))
            await session.flush()
            return
        if any(getattr(record, key) != value for key, value in values.items()):
            raise IdempotencyConflictError("开发 seed 与现有数据冲突")

    @staticmethod
    def _to_message(record: ConversationMessageRecord) -> StoredMessage:
        return StoredMessage(
            id=record.id,
            visit_matter_id=record.visit_matter_id,
            participant_id=record.participant_id,
            turn_id=record.turn_id,
            role=cast(MessageRole, record.role),
            state=cast(MessageState, record.state),
            content=record.content,
            sequence=record.sequence,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
