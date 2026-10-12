"""add_provider_webhook_events

Revision ID: 0002_add_provider_webhook_events
Revises: 0001_initial_schema
Create Date: 2026-10-12 11:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_add_provider_webhook_events"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_webhook_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_account_id", sa.String(length=128), server_default="", nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("entity_reference", sa.String(length=128), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "provider_account_id",
            "event_id",
            name="uq_provider_webhook_events_provider_account_event",
        ),
    )
    op.create_index(
        op.f("ix_provider_webhook_events_provider"),
        "provider_webhook_events",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_webhook_events_provider_account_id"),
        "provider_webhook_events",
        ["provider_account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_webhook_events_event_id"),
        "provider_webhook_events",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_provider_webhook_events_status"),
        "provider_webhook_events",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_provider_webhook_events_provider_account_event",
        "provider_webhook_events",
        ["provider", "provider_account_id", "event_id"],
        unique=False,
    )
    op.create_index(
        "ix_provider_webhook_status_received",
        "provider_webhook_events",
        ["status", "received_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_provider_webhook_status_received", table_name="provider_webhook_events")
    op.drop_index(
        "ix_provider_webhook_events_provider_account_event",
        table_name="provider_webhook_events",
    )
    op.drop_index(op.f("ix_provider_webhook_events_status"), table_name="provider_webhook_events")
    op.drop_index(op.f("ix_provider_webhook_events_event_id"), table_name="provider_webhook_events")
    op.drop_index(
        op.f("ix_provider_webhook_events_provider_account_id"),
        table_name="provider_webhook_events",
    )
    op.drop_index(op.f("ix_provider_webhook_events_provider"), table_name="provider_webhook_events")
    op.drop_table("provider_webhook_events")
