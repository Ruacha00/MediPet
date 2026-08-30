"""Create patients, visit participants, visit matters, and conversation messages."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_conversation_persistence"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "patients",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_patients")),
    )
    op.create_table(
        "visit_participants",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("patient_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_visit_participants_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visit_participants")),
        sa.UniqueConstraint("id", "patient_id", name="participant_patient"),
    )
    op.create_table(
        "visit_matters",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("patient_id", sa.String(length=128), nullable=False),
        sa.Column("participant_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("latest_message_sequence", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["participant_id", "patient_id"],
            ["visit_participants.id", "visit_participants.patient_id"],
            name="fk_visit_matters_participant_patient",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_visit_matters_patient_id_patients"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visit_matters")),
        sa.UniqueConstraint("id", "participant_id", name="visit_participant"),
    )
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("visit_matter_id", sa.String(length=128), nullable=False),
        sa.Column("participant_id", sa.String(length=128), nullable=False),
        sa.Column("turn_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "sequence",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(role = 'user' AND state = 'completed') OR role = 'assistant'",
            name=op.f("ck_conversation_messages_participant_completed"),
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name=op.f("ck_conversation_messages_role"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'streaming', 'completed', 'failed', 'cancelled')",
            name=op.f("ck_conversation_messages_state"),
        ),
        sa.ForeignKeyConstraint(
            ["visit_matter_id", "participant_id"],
            ["visit_matters.id", "visit_matters.participant_id"],
            name="fk_messages_visit_participant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_messages")),
        sa.UniqueConstraint("sequence", name=op.f("uq_conversation_messages_sequence")),
        sa.UniqueConstraint(
            "visit_matter_id",
            "turn_id",
            "role",
            name="turn_role",
        ),
    )
    op.create_index(
        "ix_messages_visit_sequence",
        "conversation_messages",
        ["visit_matter_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_visit_sequence", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_table("visit_matters")
    op.drop_table("visit_participants")
    op.drop_table("patients")
