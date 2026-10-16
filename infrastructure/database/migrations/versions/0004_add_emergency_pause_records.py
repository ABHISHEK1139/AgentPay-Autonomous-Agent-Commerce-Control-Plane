"""add_emergency_pause_records

Revision ID: 0004_add_emergency_pause_records
Revises: 0003_approval_binding
Create Date: 2026-10-12 14:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_add_emergency_pause_records"
down_revision: str | Sequence[str] | None = "0003_approval_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "emergency_pause_records",
        sa.Column("scope", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("is_paused", sa.Boolean(), nullable=False, default=True),
        sa.Column("paused_by", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resumed_by", sa.String(length=64), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("emergency_pause_records")
