"""Empirical adversarial stress-testing harness for least-privilege ApiTestClient (§14, §21, §67).

Exhaustively probes:
1. Probe 1: Base unprivileged client calling emergency routes (/status, /pause, /resume) across HTTP methods.
2. Probe 2: Unauthenticated client (.as_unauthenticated()) rejected with HTTP 401 UNAUTHENTICATED.
3. Probe 3: Agent client (.as_agent()) rejected with HTTP 403 UNAUTHORIZED_OPERATOR despite impersonation names.
4. Probe 4: Immutability, clone isolation, and anti-elevation across clone and token caches.
5. Probe 5: Multithreaded concurrent race-condition attack (50 threads racing base vs admin client).
"""

from __future__ import annotations

import concurrent.futures
import sys
import threading
from pathlib import Path
from typing import Any

# Ensure project paths are available
sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))

from test_purchase_flow import setup_orchestrator  # type: ignore[import-not-found]

from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.domain.identity import UserRole


def log(msg: str) -> None:
    """Output message to stdout without triggering lint warnings."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


# ===========================================================================
# Probe 1: Base Unprivileged Client on Emergency Endpoints
# ===========================================================================
def probe_base_client_emergency_fail_closed() -> dict[str, Any]:
    """Verify base ApiTestClient receives 403 UNAUTHORIZED_OPERATOR on all emergency endpoints."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app)

    violations: list[str] = []

    # 1. Base client default role invariant
    if client.default_role != UserRole.BUYER:
        violations.append(f"Expected default_role BUYER, got {client.default_role}")
    if client._auth_mode != "default":
        violations.append(f"Expected _auth_mode 'default', got {client._auth_mode}")

    # 2. GET /api/v1/emergency/status
    s_stat, b_stat = client.get("/api/v1/emergency/status")
    if s_stat != 403:
        violations.append(f"GET /emergency/status returned {s_stat}, expected 403")
    if b_stat.get("error", {}).get("code") != "UNAUTHORIZED_OPERATOR":
        violations.append(
            f"GET /emergency/status error code was {b_stat.get('error', {}).get('code')}"
        )

    # 3. POST /api/v1/emergency/pause with spoofed body
    s_pause, b_pause = client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "usr_test_admin", "reason": "malicious unprivileged pause"},
    )
    if s_pause != 403:
        violations.append(f"POST /emergency/pause returned {s_pause}, expected 403")
    if b_pause.get("error", {}).get("code") != "UNAUTHORIZED_OPERATOR":
        violations.append(
            f"POST /emergency/pause error code was {b_pause.get('error', {}).get('code')}"
        )
    if app.is_emergency_paused:
        violations.append("App was paused by unprivileged client POST!")

    # 4. POST /api/v1/emergency/resume
    s_res, b_res = client.post(
        "/api/v1/emergency/resume",
        json_body={"operator_id": "usr_test_admin"},
    )
    if s_res != 403:
        violations.append(f"POST /emergency/resume returned {s_res}, expected 403")
    if b_res.get("error", {}).get("code") != "UNAUTHORIZED_OPERATOR":
        violations.append(
            f"POST /emergency/resume error code was {b_res.get('error', {}).get('code')}"
        )

    # 5. Non-standard HTTP methods on emergency routes
    for method, path in [
        ("GET", "/api/v1/emergency/pause"),
        ("GET", "/api/v1/emergency/resume"),
        ("POST", "/api/v1/emergency/status"),
        ("DELETE", "/api/v1/emergency/pause"),
        ("PUT", "/api/v1/emergency/status"),
        ("PATCH", "/api/v1/emergency/resume"),
        ("GET", "/api/v1/emergency/subpath_unknown"),
    ]:
        s_m, b_m = client.request(method, path)
        if s_m != 403:
            violations.append(f"{method} {path} returned {s_m}, expected 403")
        if b_m.get("error", {}).get("code") != "UNAUTHORIZED_OPERATOR":
            violations.append(f"{method} {path} error code was {b_m.get('error', {}).get('code')}")

    return {
        "probe": "probe_base_client_emergency_fail_closed",
        "passed": len(violations) == 0,
        "violations": violations,
    }


# ===========================================================================
# Probe 2: Unauthenticated Client (.as_unauthenticated())
# ===========================================================================
def probe_unauthenticated_client_fail_closed() -> dict[str, Any]:
    """Verify unauthenticated client receives HTTP 401 UNAUTHENTICATED."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_unauthenticated()

    violations: list[str] = []

    # 1. Emergency status
    s_stat, b_stat = client.get("/api/v1/emergency/status")
    if s_stat != 401:
        violations.append(f"Unauthenticated GET /emergency/status returned {s_stat}, expected 401")
    if b_stat.get("error", {}).get("code") != "UNAUTHENTICATED":
        violations.append(
            f"Unauthenticated GET /emergency/status code: {b_stat.get('error', {}).get('code')}"
        )

    # 2. Emergency pause
    s_pause, b_pause = client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "usr_test_admin", "reason": "unauthenticated pause"},
    )
    if s_pause != 401:
        violations.append(f"Unauthenticated POST /emergency/pause returned {s_pause}, expected 401")
    if b_pause.get("error", {}).get("code") != "UNAUTHENTICATED":
        violations.append(
            f"Unauthenticated POST /emergency/pause code: {b_pause.get('error', {}).get('code')}"
        )

    # 3. Emergency resume
    s_res, b_res = client.post("/api/v1/emergency/resume")
    if s_res != 401:
        violations.append(f"Unauthenticated POST /emergency/resume returned {s_res}, expected 401")
    if b_res.get("error", {}).get("code") != "UNAUTHENTICATED":
        violations.append(
            f"Unauthenticated POST /emergency/resume code: {b_res.get('error', {}).get('code')}"
        )

    # 4. Standard protected route without credentials
    s_cat, _ = client.get("/agent/catalog", headers={"x-tenant-id": "tenant_01"})
    if s_cat != 401:
        violations.append(f"Unauthenticated GET /agent/catalog returned {s_cat}, expected 401")

    # 5. Admin stripped to unauthenticated via fluent chaining
    stripped_client = ApiTestClient(app).as_admin().as_unauthenticated()
    s_strip, _ = stripped_client.get("/api/v1/emergency/status")
    if s_strip != 401:
        violations.append(
            f"Stripped admin client GET /emergency/status returned {s_strip}, expected 401"
        )

    return {
        "probe": "probe_unauthenticated_client_fail_closed",
        "passed": len(violations) == 0,
        "violations": violations,
    }


# ===========================================================================
# Probe 3: Agent Client (.as_agent())
# ===========================================================================
def probe_agent_client_fail_closed() -> dict[str, Any]:
    """Verify agent client receives 403 UNAUTHORIZED_OPERATOR despite impersonation names."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    base_client = ApiTestClient(app)

    violations: list[str] = []

    # 1. Standard agent
    agent_client = base_client.as_agent("agent_01")
    s_stat, b_stat = agent_client.get("/api/v1/emergency/status")
    if s_stat != 403:
        violations.append(f"Agent GET /emergency/status returned {s_stat}, expected 403")
    if b_stat.get("error", {}).get("code") != "UNAUTHORIZED_OPERATOR":
        violations.append(
            f"Agent GET /emergency/status code: {b_stat.get('error', {}).get('code')}"
        )

    # 2. Agent emergency pause attempt
    s_pause, _ = agent_client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "agent_01", "reason": "agent rogue pause"},
    )
    if s_pause != 403:
        violations.append(f"Agent POST /emergency/pause returned {s_pause}, expected 403")
    if app.is_emergency_paused:
        violations.append("App was paused by agent client POST!")

    # 3. Impersonation: agent_id named 'usr_test_admin' or 'admin'
    spoofed_agent = base_client.as_agent("usr_test_admin")
    s_sp_stat, b_sp_stat = spoofed_agent.get("/api/v1/emergency/status")
    if s_sp_stat != 403:
        violations.append(
            f"Impersonating agent GET /emergency/status returned {s_sp_stat}, expected 403"
        )
    if b_sp_stat.get("error", {}).get("code") != "UNAUTHORIZED_OPERATOR":
        violations.append(
            f"Impersonating agent error code: {b_sp_stat.get('error', {}).get('code')}"
        )

    # 4. Agent can access normal catalog when scoped with tenant header
    s_cat, b_cat = agent_client.get("/agent/catalog", headers={"x-tenant-id": "tenant_01"})
    if s_cat != 200 or "products" not in b_cat:
        violations.append(f"Agent GET /agent/catalog expected 200, got {s_cat}")

    return {
        "probe": "probe_agent_client_fail_closed",
        "passed": len(violations) == 0,
        "violations": violations,
    }


# ===========================================================================
# Probe 4: Immutability, Clone Isolation, and Anti-Escalation
# ===========================================================================
def probe_immutability_and_anti_escalation() -> dict[str, Any]:
    """Verify calling .as_admin() does NOT mutate, elevate, or pollute the original client."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    base_client = ApiTestClient(app)

    violations: list[str] = []

    # 1. Clone identity separation
    admin_client = base_client.as_admin()
    if id(admin_client) == id(base_client):
        violations.append(
            "admin_client and base_client have identical object id (shallow mutation!)"
        )

    # 2. Original client attribute invariants
    if base_client.default_role != UserRole.BUYER:
        violations.append(f"base_client default_role mutated to {base_client.default_role}")
    if base_client._auth_mode != "default":
        violations.append(f"base_client _auth_mode mutated to {base_client._auth_mode}")
    if base_client._active_principal is not None:
        violations.append(
            f"base_client _active_principal mutated to {base_client._active_principal}"
        )
    if base_client._active_role is not None:
        violations.append(f"base_client _active_role mutated to {base_client._active_role}")

    # 3. Successful administrative action on admin_client
    s_adm_pause, _ = admin_client.post(
        "/api/v1/emergency/pause",
        json_body={"operator_id": "usr_test_admin", "reason": "authorized pause"},
    )
    if s_adm_pause != 200 or not app.is_emergency_paused:
        violations.append("admin_client failed to pause emergency system")

    # 4. CRITICAL: Original base_client attempts to resume or view status while paused
    s_orig_stat, _ = base_client.get("/api/v1/emergency/status")
    if s_orig_stat != 403:
        violations.append(
            f"Original base_client received {s_orig_stat} on /emergency/status after admin call, expected 403"
        )

    s_orig_res, _ = base_client.post(
        "/api/v1/emergency/resume",
        json_body={"operator_id": "usr_test_buyer"},
    )
    if s_orig_res != 403:
        violations.append(
            f"Original base_client resumed system with HTTP {s_orig_res}, expected 403"
        )
    if not app.is_emergency_paused:
        violations.append("Emergency pause was improperly deactivated by base_client!")

    # 5. Clean resumption using admin_client
    s_adm_res, _ = admin_client.post(
        "/api/v1/emergency/resume",
        json_body={"operator_id": "usr_test_admin"},
    )
    if s_adm_res != 200 or app.is_emergency_paused:
        violations.append("admin_client failed to resume emergency system")

    # 6. Token cache separation: verify base client did NOT reuse admin token
    # Partition keys: "tenant_01:usr_test_admin:ADMIN" vs "tenant_01:usr_test_buyer:BUYER"
    admin_token_key = f"tenant_01:usr_test_admin:{UserRole.ADMIN.value}"
    buyer_token_key = f"tenant_01:usr_test_buyer:{UserRole.BUYER.value}"
    if admin_token_key not in base_client._tokens_by_tenant:
        violations.append(f"Missing admin key {admin_token_key} in shared token cache")
    if buyer_token_key not in base_client._tokens_by_tenant:
        violations.append(f"Missing buyer key {buyer_token_key} in shared token cache")

    admin_tok = base_client._tokens_by_tenant.get(admin_token_key)
    buyer_tok = base_client._tokens_by_tenant.get(buyer_token_key)
    if not admin_tok or not buyer_tok or admin_tok == buyer_tok:
        violations.append(
            "Token collision: admin and buyer share the exact same token value or are empty!"
        )

    # 7. Deep multi-branch cloning isolation
    c_base = ApiTestClient(app)
    c_admin = c_base.as_admin()
    c_buyer = c_admin.as_buyer()
    c_agent = c_admin.as_agent("agent_isolated")
    c_unauth = c_admin.as_unauthenticated()

    if c_admin.get("/api/v1/emergency/status")[0] != 200:
        violations.append("c_admin failed emergency status")
    if c_buyer.get("/api/v1/emergency/status")[0] != 403:
        violations.append("c_buyer elevated to admin on emergency status")
    if c_agent.get("/api/v1/emergency/status")[0] != 403:
        violations.append("c_agent elevated to admin on emergency status")
    if c_unauth.get("/api/v1/emergency/status")[0] != 401:
        violations.append("c_unauth failed closed on emergency status")
    if c_base.get("/api/v1/emergency/status")[0] != 403:
        violations.append("c_base elevated to admin on emergency status")

    return {
        "probe": "probe_immutability_and_anti_escalation",
        "passed": len(violations) == 0,
        "violations": violations,
    }


# ===========================================================================
# Probe 5: Multithreaded Concurrent Race-Condition Attack
# ===========================================================================
def probe_multithreaded_concurrency_race_condition() -> dict[str, Any]:
    """Test 50 threads racing base unprivileged vs admin client calls simultaneously."""
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    shared_base_client = ApiTestClient(app)

    num_threads = 50
    barrier = threading.Barrier(num_threads)
    results: list[tuple[str, int, str]] = []
    lock = threading.Lock()

    def worker(worker_id: int, is_admin: bool) -> None:
        barrier.wait()
        client = shared_base_client.as_admin() if is_admin else shared_base_client

        status, body = client.get("/api/v1/emergency/status")
        code = body.get("error", {}).get("code", "OK" if status == 200 else "ERROR")
        with lock:
            results.append(("admin" if is_admin else "base", status, str(code)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [
            executor.submit(worker, i, i % 2 == 0)  # 25 admin, 25 base
            for i in range(num_threads)
        ]
        concurrent.futures.wait(futures)

    violations: list[str] = []
    base_passes = 0
    admin_passes = 0

    for role, status, code in results:
        if role == "base":
            if status != 403:
                violations.append(
                    f"Concurrent base worker returned {status} ({code}), expected 403"
                )
            elif code != "UNAUTHORIZED_OPERATOR":
                violations.append(f"Concurrent base worker error code was {code}")
            else:
                base_passes += 1
        elif role == "admin":
            if status != 200:
                violations.append(
                    f"Concurrent admin worker returned {status} ({code}), expected 200"
                )
            else:
                admin_passes += 1

    if base_passes != 25:
        violations.append(f"Expected 25 passing base probes, got {base_passes}")
    if admin_passes != 25:
        violations.append(f"Expected 25 passing admin probes, got {admin_passes}")

    # Verify shared_base_client remained unmutated after all concurrent calls
    if shared_base_client.default_role != UserRole.BUYER:
        violations.append("shared_base_client was mutated during concurrent execution!")

    return {
        "probe": "probe_multithreaded_concurrency_race_condition",
        "passed": len(violations) == 0,
        "base_passes": base_passes,
        "admin_passes": admin_passes,
        "violations": violations,
    }


def main() -> None:
    log("==================================================================")
    log("AGENTPAY EMPIRICAL LEAST-PRIVILEGE CLIENT ADVERSARIAL HARNESS")
    log("==================================================================")

    probes = [
        probe_base_client_emergency_fail_closed,
        probe_unauthenticated_client_fail_closed,
        probe_agent_client_fail_closed,
        probe_immutability_and_anti_escalation,
        probe_multithreaded_concurrency_race_condition,
    ]

    all_passed = True
    for p in probes:
        res = p()
        name = res["probe"]
        passed = res["passed"]
        if passed:
            log(f"[*] {name}: PASS")
        else:
            all_passed = False
            log(f"[!] {name}: FAIL")
            for v in res.get("violations", []):
                log(f"    - Violation: {v}")

    log("==================================================================")
    if all_passed:
        log("[+] VERDICT: ALL INVARIANTS SATISFIED -> APPROVE")
        sys.exit(0)
    else:
        log("[-] VERDICT: VULNERABILITIES DETECTED -> REJECT")
        sys.exit(1)


if __name__ == "__main__":
    main()
