from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class PatientRecord(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class VisitParticipantRecord(Base):
    __tablename__ = "visit_participants"
    __table_args__ = (UniqueConstraint("id", "patient_id", name="participant_patient"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("patients.id", ondelete="RESTRICT"),
    )
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class VisitMatterRecord(Base):
    __tablename__ = "visit_matters"
    __table_args__ = (
        ForeignKeyConstraint(
            ("participant_id", "patient_id"),
            ("visit_participants.id", "visit_participants.patient_id"),
            name="fk_visit_matters_participant_patient",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "participant_id", name="visit_participant"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("patients.id", ondelete="RESTRICT"),
    )
    participant_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(200))
    latest_message_sequence: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class ConversationMessageRecord(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ("visit_matter_id", "participant_id"),
            ("visit_matters.id", "visit_matters.participant_id"),
            name="fk_messages_visit_participant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="role",
        ),
        CheckConstraint(
            "state IN ('pending', 'streaming', 'completed', 'failed', 'cancelled')",
            name="state",
        ),
        CheckConstraint(
            "(role = 'user' AND state = 'completed') OR role = 'assistant'",
            name="participant_completed",
        ),
        UniqueConstraint("visit_matter_id", "turn_id", "role", name="turn_role"),
        Index("ix_messages_visit_sequence", "visit_matter_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    visit_matter_id: Mapped[str] = mapped_column(String(128))
    participant_id: Mapped[str] = mapped_column(String(128))
    turn_id: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    sequence: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
