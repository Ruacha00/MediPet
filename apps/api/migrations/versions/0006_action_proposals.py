"""Persist effectful Tool proposals, receipts, and audits."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_action_proposals"
down_revision: str | None = "0005_trusted_tools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_proposals",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("visit_matter_id", sa.String(128), nullable=False),
        sa.Column("participant_id", sa.String(128), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("tool_id", sa.String(128), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("tool_version", sa.String(64), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("receipt_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected', 'expired')",
            name="status",
        ),
        sa.ForeignKeyConstraint(
            ["visit_matter_id", "participant_id"],
            ["visit_matters.id", "visit_matters.participant_id"],
            name="fk_action_proposals_visit_participant",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "visit_matter_id",
            "participant_id",
            "request_key",
            name="action_proposal_request",
        ),
    )
    op.create_index(
        op.f("ix_action_proposals_expires_at"),
        "action_proposals",
        ["expires_at"],
    )
    op.create_table(
        "action_receipts",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("proposal_id", sa.String(128), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["action_proposals.id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "action_audits",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("proposal_id", sa.String(128), nullable=False),
        sa.Column("participant_id", sa.String(128), nullable=False),
        sa.Column("visit_matter_id", sa.String(128), nullable=False),
        sa.Column("decision_key", sa.String(128), nullable=False),
        sa.Column("receipt_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_action_audits_proposal_created",
        "action_audits",
        ["proposal_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_action_audits_proposal_created", table_name="action_audits")
    op.drop_table("action_audits")
    op.drop_table("action_receipts")
    op.drop_index(op.f("ix_action_proposals_expires_at"), table_name="action_proposals")
    op.drop_table("action_proposals")
