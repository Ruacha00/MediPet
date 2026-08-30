"""Align persisted message roles with visit-participant vocabulary."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_participant_message_role"
down_revision: str | None = "0001_conversation_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "conversation_messages"
ROLE_CONSTRAINT = "ck_conversation_messages_role"
PARTICIPANT_COMPLETED_CONSTRAINT = "ck_conversation_messages_participant_completed"


def upgrade() -> None:
    _drop_role_constraints()
    op.execute(sa.text("UPDATE conversation_messages SET role = 'participant' WHERE role = 'user'"))
    _create_role_constraints("participant")


def downgrade() -> None:
    _drop_role_constraints()
    op.execute(sa.text("UPDATE conversation_messages SET role = 'user' WHERE role = 'participant'"))
    _create_role_constraints("user")


def _drop_role_constraints() -> None:
    op.drop_constraint(
        op.f(PARTICIPANT_COMPLETED_CONSTRAINT),
        TABLE_NAME,
        type_="check",
    )
    op.drop_constraint(op.f(ROLE_CONSTRAINT), TABLE_NAME, type_="check")


def _create_role_constraints(participant_role: str) -> None:
    op.create_check_constraint(
        op.f(ROLE_CONSTRAINT),
        TABLE_NAME,
        f"role IN ('{participant_role}', 'assistant')",
    )
    op.create_check_constraint(
        op.f(PARTICIPANT_COMPLETED_CONSTRAINT),
        TABLE_NAME,
        f"(role = '{participant_role}' AND state = 'completed') OR role = 'assistant'",
    )
