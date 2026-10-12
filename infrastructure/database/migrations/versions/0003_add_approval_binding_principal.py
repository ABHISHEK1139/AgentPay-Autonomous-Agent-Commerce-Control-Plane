"""add_approval_binding_principal

Revision ID: 0003_add_approval_binding_principal
Revises: 0002_add_provider_webhook_events
Create Date: 2026-10-12 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_add_approval_binding_principal"
down_revision: str | Sequence[str] | None = "0002_add_provider_webhook_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.add_column(
            sa.Column("principal_id", sa.String(length=64), nullable=False, server_default="")
        )
        batch_op.create_index(
            "ix_approvals_principal_id",
            ["principal_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_approvals_tenant_principal",
            ["tenant_id", "principal_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.drop_index("ix_approvals_tenant_principal")
        batch_op.drop_index("ix_approvals_principal_id")
        batch_op.drop_column("principal_id")
