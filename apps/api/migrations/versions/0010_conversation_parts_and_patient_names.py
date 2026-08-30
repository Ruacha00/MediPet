"""Persist structured chat parts and proposal patient display names.

Revision ID: 0010_conversation_parts_and_patient_names
Revises: 0009_authoritative_action_confirmations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_conversation_parts_and_patient_names"
down_revision: str | None = "0009_authoritative_action_confirmations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column("selected_slot_id", sa.String(256), nullable=True),
    )
    op.add_column(
        "conversation_messages",
        sa.Column(
            "parts",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "action_proposals",
        sa.Column("patient_display_name", sa.String(200), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE action_proposals AS proposal
            SET patient_display_name = patient.display_name
            FROM patients AS patient
            WHERE proposal.patient_id = patient.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE action_proposals
            SET patient_display_name = '当前患者'
            WHERE patient_display_name IS NULL
            """
        )
    )
    op.alter_column("action_proposals", "patient_display_name", nullable=False)


def downgrade() -> None:
    op.drop_column("action_proposals", "patient_display_name")
    op.drop_column("conversation_messages", "parts")
    op.drop_column("conversation_messages", "selected_slot_id")
