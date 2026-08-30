from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    LargeBinary,
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
    visit_stage: Mapped[str] = mapped_column(String(16), default="pre_visit")
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
            "role IN ('participant', 'assistant')",
            name="role",
        ),
        CheckConstraint(
            "state IN ('pending', 'streaming', 'completed', 'failed', 'cancelled')",
            name="state",
        ),
        CheckConstraint(
            "(role = 'participant' AND state = 'completed') OR role = 'assistant'",
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


class RunAuditRecord(Base):
    __tablename__ = "run_audits"
    __table_args__ = (Index("ix_run_audits_turn_created", "turn_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    trace_id: Mapped[str] = mapped_column(String(128))
    visit_matter_id: Mapped[str] = mapped_column(String(128), index=True)
    turn_id: Mapped[str] = mapped_column(String(128))
    profile_version: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class RunMetricRecord(Base):
    __tablename__ = "run_metrics"
    __table_args__ = (
        Index(
            "ix_run_metrics_model_profile_created",
            "provider",
            "model",
            "profile_version",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(256))
    profile_version: Mapped[str] = mapped_column(String(128))
    first_token_ms: Mapped[float | None] = mapped_column()
    total_ms: Mapped[float] = mapped_column()
    model_ms: Mapped[float] = mapped_column()
    tool_ms: Mapped[float] = mapped_column()
    model_requests: Mapped[int] = mapped_column()
    input_tokens: Mapped[int] = mapped_column()
    output_tokens: Mapped[int] = mapped_column()
    agent_steps: Mapped[int] = mapped_column()
    outcome: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class SkillRecord(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SkillVersionRecord(Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'in_review', 'published', 'retired', 'quarantined')",
            name="status",
        ),
        UniqueConstraint("skill_id", "version", name="skill_version"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("skills.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    instructions: Mapped[str] = mapped_column(Text)
    change_note: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    governance: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    quarantine_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    publish_blockers: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SkillResourceRecord(Base):
    __tablename__ = "skill_resources"
    __table_args__ = (UniqueConstraint("skill_version_id", "path", name="skill_resource_path"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    skill_version_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("skill_versions.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(512))
    media_type: Mapped[str] = mapped_column(String(128))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SkillAuditRecord(Base):
    __tablename__ = "skill_audits"
    __table_args__ = (Index("ix_skill_audits_skill_created", "skill_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action: Mapped[str] = mapped_column(String(64))
    skill_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("skills.id", ondelete="CASCADE")
    )
    version: Mapped[int | None] = mapped_column(BigInteger)
    actor: Mapped[str] = mapped_column(String(128))
    visit_matter_id: Mapped[str | None] = mapped_column(String(128))
    turn_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolRecord(Base):
    __tablename__ = "tools"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolVersionRecord(Base):
    __tablename__ = "tool_versions"
    __table_args__ = (UniqueConstraint("tool_id", "version", name="tool_version"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tool_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("tools.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    input_schema: Mapped[dict[str, object]] = mapped_column(JSON)
    output_schema: Mapped[dict[str, object]] = mapped_column(JSON)
    effect: Mapped[str] = mapped_column(String(8))
    allowed_stages: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    provider_approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolBindingRecord(Base):
    __tablename__ = "tool_bindings"
    __table_args__ = (
        UniqueConstraint(
            "skill_id", "skill_version", "tool_id", "tool_version", name="skill_tool_binding"
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    skill_id: Mapped[str] = mapped_column(String(128))
    skill_version: Mapped[int] = mapped_column(BigInteger)
    tool_id: Mapped[str] = mapped_column(String(128))
    tool_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ToolAuditRecord(Base):
    __tablename__ = "tool_audits"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(128))
    tool_id: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[str | None] = mapped_column(String(64))
    visit_matter_id: Mapped[str | None] = mapped_column(String(128))
    turn_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActionProposalRecord(Base):
    __tablename__ = "action_proposals"
    __table_args__ = (
        ForeignKeyConstraint(
            ("visit_matter_id", "participant_id"),
            ("visit_matters.id", "visit_matters.participant_id"),
            name="fk_action_proposals_visit_participant",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "visit_matter_id",
            "participant_id",
            "request_key",
            name="action_proposal_request",
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected', 'expired')",
            name="status",
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    visit_matter_id: Mapped[str] = mapped_column(String(128))
    participant_id: Mapped[str] = mapped_column(String(128))
    request_key: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    tool_id: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(128))
    tool_version: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict[str, object]] = mapped_column(JSON)
    profile_version: Mapped[str] = mapped_column(String(128))
    visit_stage: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    receipt_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ActionReceiptRecord(Base):
    __tablename__ = "action_receipts"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("action_proposals.id", ondelete="RESTRICT"),
        unique=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    result: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionAuditRecord(Base):
    __tablename__ = "action_audits"
    __table_args__ = (Index("ix_action_audits_proposal_created", "proposal_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action: Mapped[str] = mapped_column(String(64))
    proposal_id: Mapped[str] = mapped_column(
        String(128)
    )
    participant_id: Mapped[str] = mapped_column(String(128))
    visit_matter_id: Mapped[str] = mapped_column(String(128))
    decision_key: Mapped[str] = mapped_column(String(128))
    receipt_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
