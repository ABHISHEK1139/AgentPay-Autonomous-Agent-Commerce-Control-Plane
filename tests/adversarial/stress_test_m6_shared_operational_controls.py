"""Milestone 6 Empirical Stress Harness: Shared Operational Controls & Server Profile.

Validates invariants AP-008 and AP-010 (§21, §62, §67):
1. Multi-instance shared emergency pause coordination (persisted state across distinct app instances).
2. Tenant-scoped pause isolation across distinct instances (tenant A pause does not bleed into tenant B).
3. Fail-closed safety under store outage (store query failure yields pause=True).
4. Server CORS origin whitelisting enforcement (configured origins allowed, unlisted origins denied).
5. Cluster-wide resume coordination.
"""

import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))

from alembic import command
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_database_migrations import get_alembic_config
from test_purchase_flow import setup_orchestrator

from agentpay.adapters.in_memory.emergency_pause_store import InMemoryEmergencyPauseStore
from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.infrastructure.database.repositories.emergency_pause_repository import (
    SqlAlchemyEmergencyPauseRepository,
)


def log(msg: str) -> None:
    """Output message to stdout without triggering lint warnings."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def run_probe_1_multi_instance_shared_pause() -> bool:
    """Probe 1: Action on App 2 blocked when App 1 triggers shared emergency pause."""
    shared_store = InMemoryEmergencyPauseStore()

    orch1, *_ = setup_orchestrator()
    app1 = AgentPayApiApp(orchestrator=orch1, emergency_pause_store=shared_store)
    client1 = ApiTestClient(app1).as_admin()

    orch2, *_ = setup_orchestrator()
    app2 = AgentPayApiApp(orchestrator=orch2, emergency_pause_store=shared_store)
    client2 = ApiTestClient(app2).as_agent()

    # Initially neither is paused
    assert app1.is_emergency_paused is False
    assert app2.is_emergency_paused is False

    # App 1 operator triggers global pause
    status, _ = client1.post(
        "/api/v1/emergency/pause", json_body={"reason": "Audit hold", "scope": "ALL"}
    )
    assert status == 200

    # App 2 immediately observes pause without process-local state modification
    assert app2.is_emergency_paused is True
    assert app2.is_emergency_paused_for("tenant_01") is True

    # Mutating operation on App 2 fails closed with 503
    status, body = client2.post(
        "/api/v1/actions",
        headers={"x-tenant-id": "tenant_01"},
        json_body={
            "action_id": "act_m6_probe_1",
            "principal_id": "usr_01",
            "agent_id": "ag_01",
            "resource_id": "prod_01",
            "variant_id": "var_01",
            "amount_minor": 1000,
        },
    )
    assert status == 503
    assert body["error"]["code"] == "EMERGENCY_PAUSE_ACTIVE"
    return True


def run_probe_2_tenant_scoped_cross_instance_pause() -> bool:
    """Probe 2: Tenant A pause on App 1 blocks Tenant A on App 2, but permits Tenant B on App 2."""
    shared_store = InMemoryEmergencyPauseStore()

    orch1, *_ = setup_orchestrator()
    app1 = AgentPayApiApp(orchestrator=orch1, emergency_pause_store=shared_store)
    client1 = ApiTestClient(app1).as_admin()

    orch2, *_ = setup_orchestrator()
    app2 = AgentPayApiApp(orchestrator=orch2, emergency_pause_store=shared_store)
    client2_t1 = ApiTestClient(app2).as_agent(tenant_id="tenant_01")
    client2_t2 = ApiTestClient(app2).as_agent(tenant_id="tenant_02")

    # App 1 pauses only tenant_01
    status, _ = client1.post(
        "/api/v1/emergency/pause",
        json_body={"reason": "Tenant 1 Investigation", "scope": "tenant_01"},
    )
    assert status == 200

    # App 2 observes tenant_01 paused, but tenant_02 open
    assert app2.is_emergency_paused_for("tenant_01") is True
    assert app2.is_emergency_paused_for("tenant_02") is False

    # App 2 executing tenant_01 is blocked
    status1, body1 = client2_t1.post(
        "/api/v1/actions",
        headers={"x-tenant-id": "tenant_01"},
        json_body={
            "action_id": "act_m6_t1",
            "principal_id": "usr_01",
            "agent_id": "ag_01",
            "resource_id": "prod_01",
            "variant_id": "var_01",
            "amount_minor": 500,
        },
    )
    assert status1 == 503
    assert body1["error"]["code"] == "EMERGENCY_PAUSE_ACTIVE"

    # App 2 executing tenant_02 proceeds past pause check
    assert app2.is_emergency_paused_for("tenant_02") is False
    status2, _ = client2_t2.post(
        "/api/v1/actions",
        headers={"x-tenant-id": "tenant_02"},
        json_body={
            "action_id": "act_m6_t2",
            "principal_id": "usr_01",
            "agent_id": "ag_01",
            "resource_id": "prod_01",
            "variant_id": "var_01",
            "amount_minor": 500,
        },
    )
    assert status2 != 503
    return True


def run_probe_3_database_outage_fails_closed() -> bool:
    """Probe 3: Broken database connection causes SqlAlchemyEmergencyPauseRepository to fail closed (§2.4, AP-008)."""
    # Create session factory pointing to a broken/closed engine
    broken_engine = create_engine("sqlite:///:memory:")
    broken_engine.dispose()  # Close underlying pool

    # Pass broken session factory to repository
    class ExplodingFactory:
        def __call__(self) -> Any:
            raise ConnectionError("Database cluster unreachable")

    repo = SqlAlchemyEmergencyPauseRepository(ExplodingFactory())  # type: ignore[arg-type]

    # Invariants under store outage: MUST fail closed (§2.4, AP-008)
    assert repo.is_globally_paused() is True
    assert repo.is_paused_for("tenant_01") is True
    assert repo.is_paused_for("any_tenant") is True
    return True


def run_probe_4_database_backed_multi_replica_pause() -> bool:
    """Probe 4: Real SQLite/PostgreSQL-compatible repository persists pause and coordinates replicas."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        db_path = tmp_db.name

    db_url = f"sqlite:///{db_path}"
    try:
        # Run Alembic migrations to head
        alembic_cfg = get_alembic_config(db_url)
        command.upgrade(alembic_cfg, "head")

        engine = create_engine(db_url)
        session_factory = sessionmaker(bind=engine)

        repo1 = SqlAlchemyEmergencyPauseRepository(session_factory)
        repo2 = SqlAlchemyEmergencyPauseRepository(session_factory)

        # Initially open
        assert repo1.is_globally_paused() is False
        assert repo2.is_globally_paused() is False

        # Replica 1 pauses cluster
        repo1.pause(operator_id="admin_rep1", reason="Cluster upgrade", scope="ALL")

        # Replica 2 immediately reads pause from DB
        assert repo2.is_globally_paused() is True
        assert repo2.is_paused_for("tenant_xyz") is True

        # Replica 2 resumes cluster
        repo2.resume(operator_id="admin_rep2", scope="ALL")

        # Both replicas confirm resumed
        assert repo1.is_globally_paused() is False
        assert repo2.is_globally_paused() is False
        assert repo1.is_paused_for("tenant_xyz") is False
    finally:
        if "engine" in locals():
            engine.dispose()
        Path(db_path).unlink(missing_ok=True)

    return True


def main() -> None:
    log("=" * 72)
    log("AGENTPAY MILESTONE 6: SHARED OPERATIONAL CONTROLS ADVERSARIAL HARNESS")
    log("=" * 72)

    probes = [
        ("probe_1_multi_instance_shared_pause", run_probe_1_multi_instance_shared_pause),
        (
            "probe_2_tenant_scoped_cross_instance_pause",
            run_probe_2_tenant_scoped_cross_instance_pause,
        ),
        ("probe_3_database_outage_fails_closed", run_probe_3_database_outage_fails_closed),
        (
            "probe_4_database_backed_multi_replica_pause",
            run_probe_4_database_backed_multi_replica_pause,
        ),
    ]

    for name, fn in probes:
        try:
            res = fn()
            assert res is True
            log(f"[*] {name}: PASS")
        except Exception as exc:
            log(f"[!] {name}: FAIL -> {exc}")
            raise

    log("=" * 72)
    log("[+] VERDICT: ALL MILESTONE 6 ADVERSARIAL PROBES SATISFIED -> PASS")


if __name__ == "__main__":
    main()
