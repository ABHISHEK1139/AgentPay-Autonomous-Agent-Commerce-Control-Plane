"""add_approval_binding_agent

Revision ID: 0006_add_approval_agent_id
Revises: 0005_add_cart_tables
Create Date: 2026-10-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_add_approval_agent_id"
down_revision: str | Sequence[str] | None = "0005_add_cart_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.add_column(
            sa.Column("agent_id", sa.String(length=64), nullable=False, server_default="")
        )
        batch_op.create_index(
            "ix_approvals_agent_id",
            ["agent_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_approvals_tenant_agent",
            ["tenant_id", "agent_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.drop_index("ix_approvals_tenant_agent")
        batch_op.drop_index("ix_approvals_agent_id")
        batch_op.drop_column("agent_id")
