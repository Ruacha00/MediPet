"""Create trusted Tool versions, bindings, and audits."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_trusted_tools"
down_revision: str | None = "0004_skill_packages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tools",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "tool_versions",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("tool_id", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        sa.Column("effect", sa.String(8), nullable=False),
        sa.Column("allowed_stages", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tool_id"], ["tools.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tool_id", "version", name="tool_version"),
    )
    op.create_index(op.f("ix_tool_versions_tool_id"), "tool_versions", ["tool_id"])
    op.create_table(
        "tool_bindings",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("skill_id", sa.String(128), nullable=False),
        sa.Column("skill_version", sa.BigInteger(), nullable=False),
        sa.Column("tool_id", sa.String(128), nullable=False),
        sa.Column("tool_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("skill_id", "skill_version", "tool_id", "tool_version", name="skill_tool_binding"),
    )
    op.create_table(
        "tool_audits",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("tool_id", sa.String(128)),
        sa.Column("version", sa.String(64)),
        sa.Column("visit_matter_id", sa.String(128)),
        sa.Column("turn_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tool_audits")
    op.drop_table("tool_bindings")
    op.drop_index(op.f("ix_tool_versions_tool_id"), table_name="tool_versions")
    op.drop_table("tool_versions")
    op.drop_table("tools")
