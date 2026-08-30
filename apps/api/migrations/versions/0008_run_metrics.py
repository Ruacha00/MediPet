"""Add de-identified run metrics.

Revision ID: 0008_run_metrics
Revises: 0007_run_audits
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_run_metrics"
down_revision: str | None = "0007_run_audits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_metrics",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("profile_version", sa.String(128), nullable=False),
        sa.Column("first_token_ms", sa.Float(), nullable=True),
        sa.Column("total_ms", sa.Float(), nullable=False),
        sa.Column("model_ms", sa.Float(), nullable=False),
        sa.Column("tool_ms", sa.Float(), nullable=False),
        sa.Column("model_requests", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("agent_steps", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_run_metrics_model_profile_created",
        "run_metrics",
        ["provider", "model", "profile_version", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_metrics_model_profile_created", table_name="run_metrics")
    op.drop_table("run_metrics")
