"""Add reversible visit matter archival.

Revision ID: 0011_visit_matter_archival
Revises: 0010_conversation_parts_and_patient_names
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_visit_matter_archival"
down_revision: str | None = "0010_conversation_parts_and_patient_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "visit_matters",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("visit_matters", "archived_at")
