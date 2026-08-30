"""Persist authoritative patient scope and action confirmation snapshots.

Revision ID: 0009_authoritative_action_confirmations
Revises: 0008_run_metrics
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_authoritative_action_confirmations"
down_revision: str | None = "0008_run_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_versions",
        sa.Column("confirmation_schema", sa.JSON(), nullable=True),
    )
    op.add_column(
        "action_proposals",
        sa.Column("patient_id", sa.String(128), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE action_proposals AS proposal
            SET patient_id = visit.patient_id
            FROM visit_matters AS visit
            WHERE proposal.visit_matter_id = visit.id
              AND proposal.participant_id = visit.participant_id
            """
        )
    )
    op.alter_column("action_proposals", "patient_id", nullable=False)
    op.create_foreign_key(
        "fk_action_proposals_patient",
        "action_proposals",
        "patients",
        ["patient_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "action_proposals",
        sa.Column("confirmation", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("action_proposals", "confirmation")
    op.drop_constraint(
        "fk_action_proposals_patient",
        "action_proposals",
        type_="foreignkey",
    )
    op.drop_column("action_proposals", "patient_id")
    op.drop_column("tool_versions", "confirmation_schema")
