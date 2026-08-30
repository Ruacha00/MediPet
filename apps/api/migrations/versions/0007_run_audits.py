"""Persist queryable run lifecycle audits."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_run_audits"
down_revision: str | None = "0006_action_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_audits",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("visit_matter_id", sa.String(128), nullable=False),
        sa.Column("turn_id", sa.String(128), nullable=False),
        sa.Column("profile_version", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(op.f("ix_run_audits_visit_matter_id"), "run_audits", ["visit_matter_id"])
    op.create_index(
        "ix_run_audits_turn_created",
        "run_audits",
        ["turn_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_audits_turn_created", table_name="run_audits")
    op.drop_index(op.f("ix_run_audits_visit_matter_id"), table_name="run_audits")
    op.drop_table("run_audits")
