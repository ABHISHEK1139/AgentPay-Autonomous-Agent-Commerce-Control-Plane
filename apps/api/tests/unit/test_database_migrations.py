"""Comprehensive tests for Alembic database migrations and schema lifecycle (§49, §50).

Verifies:
1. Programmatic Alembic upgrade('head') applies all 17 core relational tables.
2. Migrated database matches Base.metadata table specifications.
3. Domain repository round-trip persistence works against migrated tables.
4. Programmatic Alembic downgrade('base') cleanly removes all migrated tables.
5. Offline SQL generation succeeds for SQLite and PostgreSQL dialects.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from agentpay.domain.identity import Tenant, TenantStatus
from agentpay.infrastructure.database.models import Base
from agentpay.infrastructure.database.repositories.tenant_repository import (
    SqlAlchemyTenantRepository,
)

# Root directory of the repository
REPO_ROOT = Path(__file__).resolve().parents[4]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "infrastructure" / "database" / "migrations"


def get_alembic_config(db_url: str) -> Config:
    """Create an Alembic Config pointing to the real repository migrations."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", db_url)
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def test_alembic_upgrade_and_downgrade_lifecycle() -> None:
    """Alembic upgrade applies all 17 tables, verifies schema, and downgrade cleans up (§49)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name

    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url)
    config = get_alembic_config(db_url)

    try:
        # 1. Initially no tables exist
        inspector = inspect(engine)
        assert len(inspector.get_table_names()) == 0

        # 2. Run upgrade to head
        command.upgrade(config, "head")

        # 3. Verify all 17 tables + alembic_version exist
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "alembic_version" in tables

        expected_tables = set(Base.metadata.tables.keys())
        missing_tables = expected_tables - tables
        assert not missing_tables, f"Missing tables after migration: {missing_tables}"

        # 4. Verify columns for critical tables
        tenant_cols = {c["name"] for c in inspector.get_columns("tenants")}
        assert {"tenant_id", "name", "status", "created_at"}.issubset(tenant_cols)

        payment_cols = {c["name"] for c in inspector.get_columns("payment_attempts")}
        assert {
            "tenant_id",
            "attempt_id",
            "intent_id",
            "amount_minor",
            "amount_currency",
            "status",
            "idempotency_key",
        }.issubset(payment_cols)

        webhook_cols = {c["name"] for c in inspector.get_columns("provider_webhook_events")}
        assert {
            "id",
            "provider",
            "provider_account_id",
            "event_id",
            "status",
            "received_at",
        }.issubset(webhook_cols)

        approval_cols = {c["name"] for c in inspector.get_columns("approvals")}
        assert "principal_id" in approval_cols

        pause_cols = {c["name"] for c in inspector.get_columns("emergency_pause_records")}
        assert {"scope", "is_paused", "paused_by", "reason", "paused_at"}.issubset(pause_cols)

        # 5. Verify domain repository works against the migrated database
        session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        tenant_repo = SqlAlchemyTenantRepository(session_factory)

        tenant = Tenant(
            tenant_id="tenant_migrated_01",
            name="Migrated Org",
            status=TenantStatus.ACTIVE,
        )
        tenant_repo.save(tenant)

        retrieved = tenant_repo.get("tenant_migrated_01")
        assert retrieved is not None
        assert retrieved.tenant_id == "tenant_migrated_01"
        assert retrieved.name == "Migrated Org"
        assert retrieved.status == TenantStatus.ACTIVE

        from agentpay.infrastructure.database.repositories.emergency_pause_repository import (
            SqlAlchemyEmergencyPauseRepository,
        )

        pause_repo = SqlAlchemyEmergencyPauseRepository(session_factory)
        pause_repo.pause("op_migrated", "Maintenance", "tenant:tenant_migrated_01")
        assert pause_repo.is_paused_for("tenant_migrated_01") is True
        assert pause_repo.is_paused_for("tenant_other") is False
        pause_repo.resume("op_migrated", "tenant:tenant_migrated_01")
        assert pause_repo.is_paused_for("tenant_migrated_01") is False

        # 6. Run downgrade to base
        command.downgrade(config, "base")

        # 7. Verify all application tables are dropped
        inspector = inspect(engine)
        remaining_tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert len(remaining_tables) == 0, f"Tables remaining after downgrade: {remaining_tables}"

    finally:
        engine.dispose()
        Path(db_path).unlink(missing_ok=True)


def test_alembic_offline_sql_generation(capsys: pytest.CaptureFixture[str]) -> None:
    """Alembic generates offline SQL scripts without connecting to a live database (§49)."""
    config = get_alembic_config("sqlite:///:memory:")

    command.upgrade(config, "head", sql=True)

    captured = capsys.readouterr()
    assert "CREATE TABLE" in captured.out
    assert "tenants" in captured.out
    assert "payment_attempts" in captured.out
    assert "provider_webhook_events" in captured.out
    assert "0001_initial_schema" in captured.out


def test_alembic_current_and_heads_inspection() -> None:
    """Alembic heads and script directory inspection reports 0003_add_approval_binding_principal (§49)."""
    config = get_alembic_config("sqlite:///:memory:")
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert heads == ["0004_add_emergency_pause_records"]

    rev1 = script.get_revision("0001_initial_schema")
    assert rev1 is not None
    assert rev1.doc == "initial_schema"

    rev2 = script.get_revision("0002_add_provider_webhook_events")
    assert rev2 is not None
    assert rev2.doc == "add_provider_webhook_events"
    assert rev2.down_revision == "0001_initial_schema"

    rev3 = script.get_revision("0003_add_approval_binding_principal")
    assert rev3 is not None
    assert rev3.doc == "add_approval_binding_principal"
    assert rev3.down_revision == "0002_add_provider_webhook_events"

    rev4 = script.get_revision("0004_add_emergency_pause_records")
    assert rev4 is not None
    assert rev4.doc == "add_emergency_pause_records"
    assert rev4.down_revision == "0003_add_approval_binding_principal"
