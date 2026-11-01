"""add_cart_tables

Revision ID: 0005_add_cart_tables
Revises: 0004_add_emergency_pause_records
Create Date: 2026-10-18 05:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_add_cart_tables"
down_revision: str | Sequence[str] | None = "0004_add_emergency_pause_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "carts",
        sa.Column("cart_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            sa.String(length=64),
            sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_id", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("merchant_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_carts_tenant_id", "carts", ["tenant_id"])
    op.create_index("ix_carts_principal_id", "carts", ["principal_id"])
    op.create_index("ix_carts_agent_id", "carts", ["agent_id"])
    op.create_index("ix_carts_tenant_status", "carts", ["tenant_id", "status"])
    op.create_index("ix_carts_tenant_principal", "carts", ["tenant_id", "principal_id"])

    op.create_table(
        "cart_items",
        sa.Column(
            "cart_id",
            sa.String(length=64),
            sa.ForeignKey("carts.cart_id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("variant_id", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
    )
    op.create_index("ix_cart_items_variant_id", "cart_items", ["variant_id"])


def downgrade() -> None:
    op.drop_index("ix_cart_items_variant_id", table_name="cart_items")
    op.drop_table("cart_items")
    op.drop_index("ix_carts_tenant_principal", table_name="carts")
    op.drop_index("ix_carts_tenant_status", table_name="carts")
    op.drop_index("ix_carts_agent_id", table_name="carts")
    op.drop_index("ix_carts_principal_id", table_name="carts")
    op.drop_index("ix_carts_tenant_id", table_name="carts")
    op.drop_table("carts")
