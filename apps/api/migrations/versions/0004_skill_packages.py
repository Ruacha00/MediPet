"""Persist Skill package resources, governance, and quarantine state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_skill_packages"
down_revision: str | None = "0003_versioned_instruction_skills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_skill_versions_status", "skill_versions", type_="check")
    op.create_check_constraint(
        op.f("ck_skill_versions_status"),
        "skill_versions",
        "status IN ('draft', 'in_review', 'published', 'retired', 'quarantined')",
    )
    op.add_column(
        "skill_versions",
        sa.Column("governance", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
    )
    op.add_column(
        "skill_versions",
        sa.Column(
            "quarantine_reasons",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE skill_versions
        SET governance = json_build_object(
            'format_version', 1,
            'display_name', name,
            'change_note', change_note,
            'risk_level', 'standard',
            'required_approvals', 0
        )
        """
    )
    op.add_column(
        "skill_versions",
        sa.Column(
            "publish_blockers",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
    )
    op.alter_column("skill_audits", "skill_id", existing_type=sa.String(128), nullable=True)
    op.alter_column("skill_audits", "version", existing_type=sa.BigInteger(), nullable=True)
    op.create_table(
        "skill_resources",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("skill_version_id", sa.String(length=128), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["skill_version_id"],
            ["skill_versions.id"],
            name=op.f("fk_skill_resources_skill_version_id_skill_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_resources")),
        sa.UniqueConstraint("skill_version_id", "path", name="skill_resource_path"),
    )
    op.create_index(
        op.f("ix_skill_resources_skill_version_id"),
        "skill_resources",
        ["skill_version_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_skill_resources_skill_version_id"), table_name="skill_resources")
    op.drop_table("skill_resources")
    op.execute("DELETE FROM skill_audits WHERE skill_id IS NULL OR version IS NULL")
    op.alter_column("skill_audits", "version", existing_type=sa.BigInteger(), nullable=False)
    op.alter_column("skill_audits", "skill_id", existing_type=sa.String(128), nullable=False)
    op.drop_column("skill_versions", "publish_blockers")
    op.drop_column("skill_versions", "quarantine_reasons")
    op.drop_column("skill_versions", "governance")
    op.drop_constraint("ck_skill_versions_status", "skill_versions", type_="check")
    op.create_check_constraint(
        op.f("ck_skill_versions_status"),
        "skill_versions",
        "status IN ('draft', 'in_review', 'published', 'retired')",
    )
