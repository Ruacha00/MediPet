"""Create versioned instruction-only Skills and audit records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_versioned_instruction_skills"
down_revision: str | None = "0002_participant_message_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skills")),
        sa.UniqueConstraint("slug", name=op.f("uq_skills_slug")),
    )
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("skill_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'in_review', 'published', 'retired')",
            name=op.f("ck_skill_versions_status"),
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skills.id"],
            name=op.f("fk_skill_versions_skill_id_skills"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_versions")),
        sa.UniqueConstraint("skill_id", "version", name="skill_version"),
    )
    op.create_index(op.f("ix_skill_versions_skill_id"), "skill_versions", ["skill_id"])
    op.create_table(
        "skill_audits",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("skill_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("visit_matter_id", sa.String(length=128), nullable=True),
        sa.Column("turn_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["skills.id"],
            name=op.f("fk_skill_audits_skill_id_skills"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_audits")),
    )
    op.create_index("ix_skill_audits_skill_created", "skill_audits", ["skill_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_skill_audits_skill_created", table_name="skill_audits")
    op.drop_table("skill_audits")
    op.drop_index(op.f("ix_skill_versions_skill_id"), table_name="skill_versions")
    op.drop_table("skill_versions")
    op.drop_table("skills")
