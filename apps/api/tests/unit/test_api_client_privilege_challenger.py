"""Empirical Adversarial Challenger Test Suite for Least-Privilege ApiTestClient (§14, §21, §67).

Probes:
1. Base unprivileged client calling emergency routes (/status, /pause, /resume) always returns 403 UNAUTHORIZED_OPERATOR.
2. Unauthenticated client (.as_unauthenticated()) always returns 401 UNAUTHENTICATED.
3. Agent client (.as_agent()) always returns 403 UNAUTHORIZED_OPERATOR.
4. Calling .as_admin() on a cloned instance does not mutate or elevate the original client or cross-pollute tokens.
5. Multithreaded race-condition stress probe between base and elevated clients.
"""

from __future__ import annotations

import concurrent.futures
import threading

from test_purchase_flow import setup_orchestrator

from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.domain.identity import UserRole


def test_adversarial_base_client_emergency_fail_closed() -> None:
    """Empirical probe: Base ApiTestClient receives 403 UNAUTHORIZED_OPERATOR across all emergency endpoints."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    # Invariant: default role is BUYER
    assert client.default_role == UserRole.BUYER
    assert client._auth_mode == "default"

    # 1. GET /api/v1/emergency/status
    s_stat, b_stat = client.get("/api/v1/emergency/status")
    assert s_stat == 403
    assert b_stat["error"]["code"] == "UNAUTHORIZED_OPERATOR"
    assert b_stat["error"]["details"]["role"] == "BUYER"
    assert "ADMIN" in b_stat["error"]["details"]["required_roles"]

    # 2. POST /api/v1/emergency/pause with spoofed admin body
    s_pause, b_pause = client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "usr_test_admin", "reason": "malicious unprivileged pause"},
    )
    assert s_pause == 403
    assert b_pause["error"]["code"] == "UNAUTHORIZED_OPERATOR"
    assert app.is_emergency_paused is False

    # 3. POST /api/v1/emergency/resume
    s_res, b_res = client.post(
        "/api/v1/emergency/resume",
        json_body={"operator_id": "usr_test_admin"},
    )
    assert s_res == 403
    assert b_res["error"]["code"] == "UNAUTHORIZED_OPERATOR"

    # 4. Probing all HTTP methods
    for method in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
        s_m, b_m = client.request(method, "/api/v1/emergency/status")
        assert s_m == 403
        assert b_m["error"]["code"] == "UNAUTHORIZED_OPERATOR"


def test_adversarial_unauthenticated_client_fail_closed() -> None:
    """Empirical probe: Unauthenticated client (.as_unauthenticated()) receives HTTP 401 UNAUTHENTICATED."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_unauthenticated()

    # 1. Emergency status
    s_stat, b_stat = client.get("/api/v1/emergency/status")
    assert s_stat == 401
    assert b_stat["error"]["code"] == "UNAUTHENTICATED"

    # 2. Emergency pause
    s_pause, b_pause = client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "usr_test_admin", "reason": "unauthenticated pause"},
    )
    assert s_pause == 401
    assert b_pause["error"]["code"] == "UNAUTHENTICATED"
    assert app.is_emergency_paused is False

    # 3. Emergency resume
    s_res, b_res = client.post("/api/v1/emergency/resume")
    assert s_res == 401
    assert b_res["error"]["code"] == "UNAUTHENTICATED"

    # 4. Standard route fails without authentication
    s_cat, b_cat = client.get("/agent/catalog", headers={"x-tenant-id": "tenant_01"})
    assert s_cat == 401
    assert b_cat["error"]["code"] == "UNAUTHENTICATED"

    # 5. Admin stripped via chaining
    stripped = ApiTestClient(app).as_admin().as_unauthenticated()
    s_strip, b_strip = stripped.get("/api/v1/emergency/status")
    assert s_strip == 401
    assert b_strip["error"]["code"] == "UNAUTHENTICATED"


def test_adversarial_agent_client_fail_closed() -> None:
    """Empirical probe: Agent client (.as_agent()) receives 403 UNAUTHORIZED_OPERATOR even with spoofed names."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    base_client = ApiTestClient(app)

    # 1. Standard agent
    agent_client = base_client.as_agent("agent_01")
    s_stat, b_stat = agent_client.get("/api/v1/emergency/status")
    assert s_stat == 403
    assert b_stat["error"]["code"] == "UNAUTHORIZED_OPERATOR"

    # 2. Agent emergency pause
    s_pause, b_pause = agent_client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "agent_01", "reason": "rogue agent pause"},
    )
    assert s_pause == 403
    assert b_pause["error"]["code"] == "UNAUTHORIZED_OPERATOR"
    assert app.is_emergency_paused is False

    # 3. Impersonation: agent named usr_test_admin
    spoofed = base_client.as_agent("usr_test_admin")
    s_sp, b_sp = spoofed.get("/api/v1/emergency/status")
    assert s_sp == 403
    assert b_sp["error"]["code"] == "UNAUTHORIZED_OPERATOR"

    # 4. Agent can access normal catalog
    s_cat, b_cat = agent_client.get("/agent/catalog", headers={"x-tenant-id": "tenant_01"})
    assert s_cat == 200
    assert "products" in b_cat


def test_adversarial_immutability_and_anti_escalation() -> None:
    """Empirical probe: Calling .as_admin() does NOT mutate or elevate original client."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    base_client = ApiTestClient(app)

    # Clone identity separation
    admin_client = base_client.as_admin()
    assert id(admin_client) != id(base_client)

    # Base client invariants
    assert base_client.default_role == UserRole.BUYER
    assert base_client._auth_mode == "default"
    assert base_client._active_principal is None
    assert base_client._active_role is None

    # Pause via admin_client
    s_pause, _ = admin_client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "usr_test_admin", "reason": "authorized incident response"},
    )
    assert s_pause == 200
    assert app.is_emergency_paused is True

    # Base client MUST NOT be elevated to resume
    s_resume_fail, b_resume_fail = base_client.post(
        "/api/v1/emergency/resume",
        json_body={"operator_id": "usr_test_buyer"},
    )
    assert s_resume_fail == 403
    assert b_resume_fail["error"]["code"] == "UNAUTHORIZED_OPERATOR"
    assert app.is_emergency_paused is True

    # Resume via admin_client
    s_resume_ok, _ = admin_client.post(
        "/api/v1/emergency/resume",
        json_body={"operator_id": "usr_test_admin"},
    )
    assert s_resume_ok == 200
    assert app._emergency_paused is False

    # Token cache partitioning: separate keys
    admin_key = f"tenant_01:usr_test_admin:{UserRole.ADMIN.value}"
    buyer_key = f"tenant_01:usr_test_buyer:{UserRole.BUYER.value}"
    assert admin_key in base_client._tokens_by_tenant
    assert buyer_key in base_client._tokens_by_tenant
    assert base_client._tokens_by_tenant[admin_key] != base_client._tokens_by_tenant[buyer_key]

    # Multi-branch cloning immutability
    c_buyer = admin_client.as_buyer()
    c_agent = admin_client.as_agent("agent_test")
    c_unauth = admin_client.as_unauthenticated()

    assert admin_client.get("/api/v1/emergency/status")[0] == 200
    assert c_buyer.get("/api/v1/emergency/status")[0] == 403
    assert c_agent.get("/api/v1/emergency/status")[0] == 403
    assert c_unauth.get("/api/v1/emergency/status")[0] == 401
    assert base_client.get("/api/v1/emergency/status")[0] == 403


def test_adversarial_multithreaded_concurrency_race_condition() -> None:
    """Empirical probe: 50 concurrent threads racing base vs admin client calls simultaneously."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    shared_base_client = ApiTestClient(app)

    num_threads = 50
    barrier = threading.Barrier(num_threads)
    results: list[tuple[str, int, str]] = []
    lock = threading.Lock()

    def worker(is_admin: bool) -> None:
        barrier.wait()
        client = shared_base_client.as_admin() if is_admin else shared_base_client
        status, body = client.get("/api/v1/emergency/status")
        code = body.get("error", {}).get("code", "OK" if status == 200 else "ERROR")
        with lock:
            results.append(("admin" if is_admin else "base", status, str(code)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, i % 2 == 0) for i in range(num_threads)]
        concurrent.futures.wait(futures)

    assert len(results) == num_threads
    for role, status, code in results:
        if role == "base":
            assert status == 403
            assert code == "UNAUTHORIZED_OPERATOR"
        else:
            assert status == 200

    assert shared_base_client.default_role == UserRole.BUYER
