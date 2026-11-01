"""add_order_cancellation_reason

Revision ID: 0007_add_order_cancellation_reason
Revises: 0006_add_approval_agent_id
Create Date: 2026-11-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_add_order_cancellation_reason"
down_revision: str | Sequence[str] | None = "0006_add_approval_agent_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("cancellation_reason", sa.String(length=256), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("cancellation_reason")
