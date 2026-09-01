from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
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
from sqlalchemy.orm.attributes import flag_modified

from medipet.agent.capabilities import VisitStage
from medipet.persistence.conversation import (
    AssistantMessageTargetState,
    DevelopmentVisitMatter,
    IdempotencyConflictError,
    MessageRole,
    MessageState,
    MessageTransitionError,
    StoredMessage,
    TerminalMessageState,
    VisitContext,
    VisitMatterArchivedError,
    VisitMatterBusyError,
    VisitMatterNotFoundError,
    VisitMatterSummary,
    VisitTurn,
    assistant_transition_source_states,
    has_pending_action_proposal,
    replace_proposal_part,
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
            await self._require_visit_matter(session, visit_matter_id, participant_id)

    async def visit_stage(self, visit_matter_id: str, participant_id: str) -> VisitStage:
        async with self._sessions() as session:
            visit_matter = await self._require_visit_matter(
                session, visit_matter_id, participant_id
            )
            return cast(VisitStage, visit_matter.visit_stage)

    async def visit_context(
        self,
        visit_matter_id: str,
        participant_id: str,
    ) -> VisitContext:
        async with self._sessions() as session:
            visit_matter = await self._require_active_visit_matter(
                session, visit_matter_id, participant_id
            )
            patient = await session.get(PatientRecord, visit_matter.patient_id)
            if patient is None:
                raise VisitMatterNotFoundError("就诊事项对应的患者不存在")
            return VisitContext(
                patient_id=visit_matter.patient_id,
                patient_display_name=patient.display_name,
                visit_stage=cast(VisitStage, visit_matter.visit_stage),
            )

    async def seed_development_visit_matter(
        self,
        visit_matter: DevelopmentVisitMatter,
    ) -> None:
        async with self._sessions.begin() as session:
            await self._seed_record(
                session,
                PatientRecord,
                visit_matter.patient_id,
                {"display_name": visit_matter.patient_display_name},
            )
            await self._seed_record(
                session,
                VisitParticipantRecord,
                visit_matter.participant_id,
                {
                    "patient_id": visit_matter.patient_id,
                    "display_name": visit_matter.participant_display_name,
                },
            )
            await self._seed_record(
                session,
                VisitMatterRecord,
                visit_matter.visit_matter_id,
                {
                    "patient_id": visit_matter.patient_id,
                    "participant_id": visit_matter.participant_id,
                    "title": visit_matter.visit_matter_title,
                    "visit_stage": visit_matter.visit_stage,
                },
            )

    async def create_visit_matter(
        self,
        *,
        participant_id: str,
        title: str,
    ) -> VisitMatterSummary:
        async with self._sessions.begin() as session:
            participant = await session.get(VisitParticipantRecord, participant_id)
            if participant is None:
                raise VisitMatterNotFoundError("就诊参与者不存在")
            patient = await session.get(PatientRecord, participant.patient_id)
            if patient is None:
                raise VisitMatterNotFoundError("就诊参与者对应的患者不存在")
            record = VisitMatterRecord(
                id=f"visit-matter-{uuid4().hex}",
                patient_id=participant.patient_id,
                participant_id=participant.id,
                title=title,
                visit_stage="pre_visit",
            )
            session.add(record)
            await session.flush()
            return VisitMatterSummary(
                visit_matter_id=record.id,
                title=record.title,
                visit_stage="pre_visit",
                patient_display_name=patient.display_name,
                participant_display_name=participant.display_name,
                archived_at=None,
            )

    async def list_visit_matters(
        self,
        participant_id: str,
        *,
        archived: bool = False,
    ) -> list[VisitMatterSummary]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        VisitMatterRecord,
                        PatientRecord.display_name,
                        VisitParticipantRecord.display_name,
                    )
                    .join(PatientRecord, PatientRecord.id == VisitMatterRecord.patient_id)
                    .join(
                        VisitParticipantRecord,
                        VisitParticipantRecord.id == VisitMatterRecord.participant_id,
                    )
                    .where(VisitMatterRecord.participant_id == participant_id)
                    .where(
                        VisitMatterRecord.archived_at.is_not(None)
                        if archived
                        else VisitMatterRecord.archived_at.is_(None)
                    )
                    .order_by(
                        VisitMatterRecord.updated_at.desc(),
                        VisitMatterRecord.created_at.desc(),
                    )
                )
            ).all()
            return [
                VisitMatterSummary(
                    visit_matter_id=record.id,
                    title=record.title,
                    visit_stage=cast(VisitStage, record.visit_stage),
                    patient_display_name=patient_display_name,
                    participant_display_name=participant_display_name,
                    archived_at=record.archived_at,
                )
                for record, patient_display_name, participant_display_name in rows
            ]

    async def rename_visit_matter(
        self,
        visit_matter_id: str,
        participant_id: str,
        *,
        title: str,
    ) -> VisitMatterSummary:
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(
                        VisitMatterRecord,
                        PatientRecord.display_name,
                        VisitParticipantRecord.display_name,
                    )
                    .join(PatientRecord, PatientRecord.id == VisitMatterRecord.patient_id)
                    .join(
                        VisitParticipantRecord,
                        VisitParticipantRecord.id == VisitMatterRecord.participant_id,
                    )
                    .where(
                        VisitMatterRecord.id == visit_matter_id,
                        VisitMatterRecord.participant_id == participant_id,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise VisitMatterNotFoundError("就诊事项不存在或参与者不匹配")
            record, patient_display_name, participant_display_name = row
            if record.title != title:
                await session.execute(
                    update(VisitMatterRecord)
                    .where(VisitMatterRecord.id == visit_matter_id)
                    .values(title=title, updated_at=VisitMatterRecord.updated_at)
                    .execution_options(synchronize_session=False)
                )
            return VisitMatterSummary(
                visit_matter_id=record.id,
                title=title,
                visit_stage=cast(VisitStage, record.visit_stage),
                patient_display_name=patient_display_name,
                participant_display_name=participant_display_name,
                archived_at=record.archived_at,
            )

    async def archive_visit_matter(
        self,
        visit_matter_id: str,
        participant_id: str,
        *,
        pending_action: bool | None = None,
    ) -> VisitMatterSummary:
        return await self._set_visit_matter_archived_at(
            visit_matter_id,
            participant_id,
            archived_at=datetime.now(UTC),
            pending_action=pending_action,
        )

    async def restore_visit_matter(
        self,
        visit_matter_id: str,
        participant_id: str,
    ) -> VisitMatterSummary:
        return await self._set_visit_matter_archived_at(
            visit_matter_id,
            participant_id,
            archived_at=None,
        )

    async def _set_visit_matter_archived_at(
        self,
        visit_matter_id: str,
        participant_id: str,
        *,
        archived_at: datetime | None,
        pending_action: bool | None = None,
    ) -> VisitMatterSummary:
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(
                        VisitMatterRecord,
                        PatientRecord.display_name,
                        VisitParticipantRecord.display_name,
                    )
                    .join(PatientRecord, PatientRecord.id == VisitMatterRecord.patient_id)
                    .join(
                        VisitParticipantRecord,
                        VisitParticipantRecord.id == VisitMatterRecord.participant_id,
                    )
                    .where(
                        VisitMatterRecord.id == visit_matter_id,
                        VisitMatterRecord.participant_id == participant_id,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise VisitMatterNotFoundError("就诊事项不存在或参与者不匹配")
            record, patient_display_name, participant_display_name = row
            if (
                archived_at is not None
                and record.archived_at is None
                and await self._visit_matter_is_busy(
                    session,
                    visit_matter_id,
                    pending_action=pending_action,
                )
            ):
                raise VisitMatterBusyError("就诊事项仍有进行中的回复或待确认操作")
            if record.archived_at != archived_at and not (
                record.archived_at is not None and archived_at is not None
            ):
                await session.execute(
                    update(VisitMatterRecord)
                    .where(VisitMatterRecord.id == visit_matter_id)
                    .values(
                        archived_at=archived_at,
                        updated_at=VisitMatterRecord.updated_at,
                    )
                    .execution_options(synchronize_session=False)
                )
            effective_archived_at = record.archived_at
            if record.archived_at is None or archived_at is None:
                effective_archived_at = archived_at
            return VisitMatterSummary(
                visit_matter_id=record.id,
                title=record.title,
                visit_stage=cast(VisitStage, record.visit_stage),
                patient_display_name=patient_display_name,
                participant_display_name=participant_display_name,
                archived_at=effective_archived_at,
            )

    async def add_participant_message(
        self,
        *,
        turn: VisitTurn,
        content: str,
        selected_slot_id: str | None = None,
    ) -> StoredMessage:
        return await self._add_message(
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
        return await self._claim_message(
            turn=turn,
            role="assistant",
            state="pending",
            content="",
        )

    async def mark_assistant_streaming(self, message_id: str) -> None:
        await self._transition(message_id, to_state="streaming")

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

    async def append_assistant_part(self, message_id: str, part: dict[str, object]) -> None:
        async with self._sessions.begin() as session:
            record = await session.scalar(
                select(ConversationMessageRecord)
                .where(ConversationMessageRecord.id == message_id)
                .with_for_update()
            )
            if (
                record is None
                or record.role != "assistant"
                or record.state not in {"pending", "streaming"}
            ):
                raise MessageTransitionError("只有进行中的助手消息可以追加结构化内容")
            record.parts = [*record.parts, dict(part)]
            flag_modified(record, "parts")

    async def update_action_proposal_part(
        self,
        visit_matter_id: str,
        proposal_id: str,
        part: dict[str, object],
    ) -> None:
        async with self._sessions.begin() as session:
            records = (
                await session.scalars(
                    select(ConversationMessageRecord)
                    .where(
                        ConversationMessageRecord.visit_matter_id == visit_matter_id,
                        ConversationMessageRecord.role == "assistant",
                    )
                    .order_by(ConversationMessageRecord.sequence)
                    .with_for_update()
                )
            ).all()
            for record in records:
                updated = replace_proposal_part(tuple(record.parts), proposal_id, part)
                if updated is None:
                    continue
                record.parts = list(updated)
                flag_modified(record, "parts")
                return
            raise MessageTransitionError("聊天历史中不存在该预约确认")

    async def has_action_proposal_part(self, visit_matter_id: str, proposal_id: str) -> bool:
        async with self._sessions() as session:
            records = (
                await session.scalars(
                    select(ConversationMessageRecord).where(
                        ConversationMessageRecord.visit_matter_id == visit_matter_id,
                        ConversationMessageRecord.role == "assistant",
                    )
                )
            ).all()
        return any(
            replace_proposal_part(tuple(record.parts), proposal_id, {}) is not None
            for record in records
        )

    async def finish_assistant_message(
        self,
        message_id: str,
        state: TerminalMessageState,
    ) -> None:
        await self._transition(message_id, to_state=state)

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
        turn: VisitTurn,
        role: MessageRole,
        state: MessageState,
        content: str,
        selected_slot_id: str | None = None,
    ) -> StoredMessage:
        message, _ = await self._claim_message(
            turn=turn,
            role=role,
            state=state,
            content=content,
            selected_slot_id=selected_slot_id,
        )
        return message

    async def _claim_message(
        self,
        *,
        turn: VisitTurn,
        role: MessageRole,
        state: MessageState,
        content: str,
        selected_slot_id: str | None = None,
    ) -> tuple[StoredMessage, bool]:
        async with self._sessions.begin() as session:
            await self._require_active_visit_matter_for_update(
                session,
                turn.visit_matter_id,
                turn.participant_id,
            )
            message_id = f"message-{uuid4().hex}"
            statement = (
                insert(ConversationMessageRecord)
                .values(
                    id=message_id,
                    visit_matter_id=turn.visit_matter_id,
                    participant_id=turn.participant_id,
                    turn_id=turn.turn_id,
                    role=role,
                    state=state,
                    content=content,
                    selected_slot_id=selected_slot_id,
                )
                .on_conflict_do_nothing(index_elements=("visit_matter_id", "turn_id", "role"))
                .returning(ConversationMessageRecord.id)
            )
            inserted_id = (await session.execute(statement)).scalar_one_or_none()
            record = await session.scalar(
                select(ConversationMessageRecord).where(
                    ConversationMessageRecord.visit_matter_id == turn.visit_matter_id,
                    ConversationMessageRecord.turn_id == turn.turn_id,
                    ConversationMessageRecord.role == role,
                )
            )
            if record is None:
                raise RuntimeError("消息写入后无法读取")
            if (
                inserted_id is None
                and role == "participant"
                and (record.content != content or record.selected_slot_id != selected_slot_id)
            ):
                raise IdempotencyConflictError("同一 turn 的参与者消息输入不一致")
            if inserted_id is not None:
                await session.execute(
                    update(VisitMatterRecord)
                    .where(VisitMatterRecord.id == turn.visit_matter_id)
                    .values(
                        latest_message_sequence=func.greatest(
                            func.coalesce(VisitMatterRecord.latest_message_sequence, 0),
                            record.sequence,
                        ),
                        updated_at=func.now(),
                    )
                )
            return self._to_message(record), inserted_id is not None

    async def _transition(
        self,
        message_id: str,
        *,
        to_state: AssistantMessageTargetState,
    ) -> None:
        from_states = assistant_transition_source_states(to_state)
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
    async def _require_visit_matter(
        session: AsyncSession,
        visit_matter_id: str,
        participant_id: str,
    ) -> VisitMatterRecord:
        visit_matter = await session.scalar(
            select(VisitMatterRecord).where(
                VisitMatterRecord.id == visit_matter_id,
                VisitMatterRecord.participant_id == participant_id,
            )
        )
        if visit_matter is None:
            raise VisitMatterNotFoundError("就诊事项不存在或参与者不匹配")
        return visit_matter

    @classmethod
    async def _require_active_visit_matter(
        cls,
        session: AsyncSession,
        visit_matter_id: str,
        participant_id: str,
    ) -> VisitMatterRecord:
        visit_matter = await cls._require_visit_matter(
            session,
            visit_matter_id,
            participant_id,
        )
        if visit_matter.archived_at is not None:
            raise VisitMatterArchivedError("就诊事项已归档，请恢复后继续")
        return visit_matter

    @staticmethod
    async def _require_active_visit_matter_for_update(
        session: AsyncSession,
        visit_matter_id: str,
        participant_id: str,
    ) -> VisitMatterRecord:
        visit_matter = await session.scalar(
            select(VisitMatterRecord)
            .where(
                VisitMatterRecord.id == visit_matter_id,
                VisitMatterRecord.participant_id == participant_id,
            )
            .with_for_update()
        )
        if visit_matter is None:
            raise VisitMatterNotFoundError("就诊事项不存在或参与者不匹配")
        if visit_matter.archived_at is not None:
            raise VisitMatterArchivedError("就诊事项已归档，请恢复后继续")
        return visit_matter

    @staticmethod
    async def _visit_matter_is_busy(
        session: AsyncSession,
        visit_matter_id: str,
        *,
        pending_action: bool | None = None,
    ) -> bool:
        records = (
            await session.scalars(
                select(ConversationMessageRecord).where(
                    ConversationMessageRecord.visit_matter_id == visit_matter_id,
                )
            )
        ).all()
        participant_turns = {
            record.turn_id for record in records if record.role == "participant"
        }
        assistant_turns = {
            record.turn_id for record in records if record.role == "assistant"
        }
        return bool(participant_turns - assistant_turns) or any(
            (record.role == "assistant" and record.state in {"pending", "streaming"})
            or (
                pending_action is None
                and has_pending_action_proposal(tuple(record.parts))
            )
            for record in records
        ) or pending_action is True

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
            selected_slot_id=record.selected_slot_id,
            parts=tuple(dict(part) for part in record.parts),
            sequence=record.sequence,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
