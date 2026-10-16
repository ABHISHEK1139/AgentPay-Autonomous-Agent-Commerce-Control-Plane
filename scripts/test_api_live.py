#!/usr/bin/env python3
"""scripts/test_api_live.py - Interactive Live HTTP API Test Harness (§10, §12, §13, §109).

Executes end-to-end integration tests over real TCP socket HTTP against
either a running AgentPay server or an ephemeral local instance.

Usage:
    uv run python scripts/test_api_live.py [--url http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure project packages and scripts can be resolved
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api" / "src"))

from agentpay.api.factory import create_app
from agentpay.server import create_server


def log_line(msg: str) -> None:
    sys.stdout.write(f"{msg}\n")
    sys.stdout.flush()


class LiveApiTester:
    def __init__(
        self, base_url: str, webhook_secret: str = "whsec_live_test_secret_32bytes_len"
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.webhook_secret = webhook_secret
        self.tenant_id = "tenant_01"
        self.admin_token = "test_bearer_admin_token"
        self.agent_key = "apk_test_agent_key_tenant_01"
        self.buyer_key = "apk_test_buyer_key_tenant_01"
        self.tests_passed = 0
        self.tests_failed = 0

    def _request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any], dict[str, str], float]:
        url = f"{self.base_url}{path}"
        req_headers = {"Accept": "application/json"}
        if headers:
            req_headers.update(headers)

        body_bytes = None
        if data is not None:
            body_bytes = json.dumps(data).encode("utf-8")
            req_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method=method)
        start_t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                elapsed_ms = (time.perf_counter() - start_t) * 1000.0
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                resp_body = json.loads(resp.read().decode("utf-8")) if resp.status != 204 else {}
                return resp.status, resp_body, resp_headers, elapsed_ms
        except urllib.error.HTTPError as err:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            resp_headers = {k.lower(): v for k, v in err.headers.items()}
            err_body = {}
            with contextlib.suppress(Exception):
                err_body = json.loads(err.read().decode("utf-8"))
            return err.code, err_body, resp_headers, elapsed_ms

    def assert_test(
        self,
        test_name: str,
        passed: bool,
        detail: str = "",
        status_code: int = 0,
        body: Any = None,
    ) -> None:
        if passed:
            self.tests_passed += 1
            log_line(f"  [PASS] {test_name:<38} {detail}")
        else:
            self.tests_failed += 1
            log_line(f"  [FAIL] {test_name:<38} (HTTP {status_code}) body={body} {detail}")

    def run_all_tests(self) -> int:
        log_line("=" * 80)
        log_line("AGENTPAY LIVE HTTP API END-TO-END VERIFICATION")
        log_line("=" * 80)
        log_line(f"Target URL: {self.base_url}")
        log_line(f"Tenant:     {self.tenant_id}")
        log_line("-" * 80)

        # 1. Health check
        st, b, _, el = self._request("GET", "/api/v1/health")
        self.assert_test(
            "1. Health Check", st == 200 and b.get("status") == "healthy", f"({el:.1f}ms)", st, b
        )

        # 2. System config diagnostics
        st, b, _, el = self._request("GET", "/api/v1/system/config")
        self.assert_test(
            "2. System Diagnostics", st == 200 and "environment" in b, f"({el:.1f}ms)", st, b
        )

        # 3. OpenAPI schema
        st, b, _, el = self._request("GET", "/openapi.json")
        self.assert_test(
            "3. OpenAPI Specification",
            st == 200 and b.get("openapi") == "3.2.0",
            f"({el:.1f}ms)",
            st,
            b,
        )

        # 4. Prometheus metrics exposition
        st, b, _, el = self._request("GET", "/api/v1/metrics")
        self.assert_test(
            "4. Prometheus Metrics",
            st == 200 and "metrics" in b and "prometheus" in b,
            f"({el:.1f}ms)",
            st,
            b,
        )

        # 5. Supply chain SBOM metadata
        st, b, _, el = self._request(
            "GET",
            "/api/v1/security/sbom",
            headers={"Authorization": f"Bearer {self.admin_token}", "X-Tenant-ID": self.tenant_id},
        )
        self.assert_test(
            "5. Supply Chain SBOM Metadata", st == 200 and "format" in b, f"({el:.1f}ms)", st, b
        )

        # 6. CORS Preflight
        st, _, h, el = self._request(
            "OPTIONS",
            "/api/v1/actions",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, X-Tenant-ID",
            },
        )
        self.assert_test(
            "6. CORS Preflight Negotiation",
            st == 204 and h.get("access-control-allow-origin") == "*",
            f"({el:.1f}ms)",
            st,
        )

        # 7. Unauthenticated request rejection
        st, b, _, el = self._request("GET", "/agent/catalog")
        self.assert_test(
            "7. Unauthenticated Access Rejection",
            st == 401 and b.get("error", {}).get("code") == "UNAUTHENTICATED",
            f"({el:.1f}ms)",
            st,
            b,
        )

        # 8. Invalid key rejection with trace_id correlation
        st, b, _, el = self._request("GET", "/agent/catalog", headers={"X-API-Key": "apk_bad_key"})
        self.assert_test(
            "8. Invalid Credentials Rejection",
            st == 401 and b.get("error", {}).get("trace_id") is not None,
            f"({el:.1f}ms)",
            st,
            b,
        )

        # 9. Cross-tenant access rejection
        st, b, _, el = self._request(
            "GET",
            "/agent/catalog",
            headers={"X-API-Key": self.buyer_key, "X-Tenant-ID": "tenant_unauthorized"},
        )
        self.assert_test(
            "9. Cross-Tenant Denial",
            st == 403 and b.get("error", {}).get("code") == "CROSS_TENANT_ACCESS_DENIED",
            f"({el:.1f}ms)",
            st,
            b,
        )

        # 10. Catalog product discovery
        st, b, _, el = self._request(
            "GET",
            "/agent/catalog",
            headers={"X-API-Key": self.buyer_key, "X-Tenant-ID": self.tenant_id},
        )
        prods = b.get("products", [])
        self.assert_test(
            "10. Catalog Product Discovery",
            st == 200 and len(prods) >= 2 and any(p["product_id"] == "prod_01" for p in prods),
            f"({len(prods)} products, {el:.1f}ms)",
            st,
            b,
        )

        # 11. Product detail inspection
        st, b, _, el = self._request(
            "GET",
            "/agent/products/prod_01",
            headers={"X-API-Key": self.buyer_key, "X-Tenant-ID": self.tenant_id},
        )
        self.assert_test(
            "11. Product Detail Inspection",
            st == 200 and b.get("name") == "Pro Laptop",
            f"({el:.1f}ms)",
            st,
            b,
        )

        # 12. Inventory lookup
        st, b, _, el = self._request(
            "GET",
            "/agent/products/var_01/inventory",
            headers={"X-API-Key": self.buyer_key, "X-Tenant-ID": self.tenant_id},
        )
        self.assert_test(
            "12. Inventory Stock Lookup",
            st == 200 and b.get("available", 0) > 0,
            f"({b.get('available')} in stock, {el:.1f}ms)",
            st,
            b,
        )

        # 13. Autonomous purchase under limit (₹1,299 < ₹2,000 threshold)
        act_id_auto = f"act_auto_{int(time.time() * 1000)}"
        st, b, _, el = self._request(
            "POST",
            "/api/v1/actions",
            headers={"X-API-Key": self.agent_key, "X-Tenant-ID": self.tenant_id},
            data={
                "action_id": act_id_auto,
                "principal_id": "user_01",
                "agent_id": "agent_01",
                "domain": "commerce",
                "action": "purchase",
                "resource_type": "product_variant",
                "resource_id": "prod_01",
                "variant_id": "var_01",
                "amount_minor": 129900,
                "currency": "INR",
                "intent_purpose": "Developer workstation acquisition",
                "quantity": 1,
            },
        )
        created_order_id = b.get("order_id")
        created_payment_id = b.get("payment_id")
        self.assert_test(
            "13. Autonomous Purchase Under Limit",
            st == 200 and b.get("status") == "COMPLETED" and created_order_id is not None,
            f"(order: {created_order_id}, {el:.1f}ms)",
            st,
            b,
        )

        # 14. Purchase exceeding limit requiring human approval (₹5,000 > ₹2,000 threshold)
        act_id_high = f"act_high_{int(time.time() * 1000)}"
        st, b, _, el = self._request(
            "POST",
            "/api/v1/actions",
            headers={"X-API-Key": self.agent_key, "X-Tenant-ID": self.tenant_id},
            data={
                "action_id": act_id_high,
                "principal_id": "user_01",
                "agent_id": "agent_01",
                "domain": "commerce",
                "action": "purchase",
                "resource_type": "product_variant",
                "resource_id": "prod_02",
                "variant_id": "var_02",
                "amount_minor": 500000,
                "currency": "INR",
                "intent_purpose": "High-value GPU cluster expansion",
                "quantity": 1,
            },
        )
        approval_id = b.get("approval_id")
        self.assert_test(
            "14. Human Approval Policy Gating",
            st == 200 and b.get("status") == "AWAITING_APPROVAL" and approval_id is not None,
            f"(approval_id: {approval_id}, {el:.1f}ms)",
            st,
            b,
        )

        # 15. Query pending approvals
        st, b, _, el = self._request(
            "GET",
            "/api/v1/approvals",
            headers={"Authorization": f"Bearer {self.admin_token}", "X-Tenant-ID": self.tenant_id},
        )
        approvals = b.get("approvals", [])
        has_appr = any(a.get("approval_id") == approval_id for a in approvals)
        self.assert_test(
            "15. Query Pending Approvals",
            st == 200 and has_appr,
            f"({len(approvals)} pending, {el:.1f}ms)",
            st,
            b,
        )

        # 16. Grant human approval
        if approval_id:
            st, b, _, el = self._request(
                "POST",
                f"/api/v1/approvals/{approval_id}/grant",
                headers={
                    "Authorization": f"Bearer {self.admin_token}",
                    "X-Tenant-ID": self.tenant_id,
                },
                data={"approver_id": "usr_admin_01", "reason": "Authorized hardware procurement"},
            )
            self.assert_test(
                "16. Grant Human Approval",
                st == 200 and b.get("status") == "APPROVED",
                f"({el:.1f}ms)",
                st,
                b,
            )

            # 17. Execute approved purchase using the approved action binding
            st, b, _, el = self._request(
                "POST",
                f"/api/v1/approvals/{approval_id}/execute",
                headers={
                    "Authorization": f"Bearer {self.admin_token}",
                    "X-Tenant-ID": self.tenant_id,
                },
                data={
                    "action_id": act_id_high,
                    "principal_id": "user_01",
                    "agent_id": "agent_01",
                    "resource_id": "prod_02",
                    "variant_id": "var_02",
                    "amount_minor": 500000,
                    "currency": "INR",
                    "quantity": 1,
                },
            )
            approved_order_id = b.get("order_id")
            self.assert_test(
                "17. Execute Approved Purchase",
                st == 200 and b.get("status") == "COMPLETED" and approved_order_id is not None,
                f"(order: {approved_order_id}, {el:.1f}ms)",
                st,
                b,
            )

            # 18. Attempt reusing already consumed approval (must be rejected with 409)
            st, b, _, el = self._request(
                "POST",
                f"/api/v1/approvals/{approval_id}/execute",
                headers={
                    "Authorization": f"Bearer {self.admin_token}",
                    "X-Tenant-ID": self.tenant_id,
                },
                data={
                    "action_id": act_id_high,
                    "principal_id": "user_01",
                    "agent_id": "agent_01",
                    "resource_id": "prod_02",
                    "variant_id": "var_02",
                    "amount_minor": 500000,
                    "currency": "INR",
                    "quantity": 1,
                },
            )
            self.assert_test(
                "18. Anti-Double-Spend Single Use",
                st == 409 and b.get("error", {}).get("code") == "APPROVAL_ALREADY_CONSUMED",
                f"({el:.1f}ms)",
                st,
                b,
            )

        # 19. Emergency Kill Switch status
        st, b, _, el = self._request(
            "GET",
            "/api/v1/emergency/status",
            headers={"Authorization": f"Bearer {self.admin_token}", "X-Tenant-ID": self.tenant_id},
        )
        self.assert_test(
            "19. Emergency Status Query",
            st == 200 and b.get("paused") is False,
            f"({el:.1f}ms)",
            st,
            b,
        )

        # 20. Emergency Kill Switch pause
        st, b, _, el = self._request(
            "POST",
            "/api/v1/emergency/pause",
            headers={"Authorization": f"Bearer {self.admin_token}", "X-Tenant-ID": self.tenant_id},
            data={"operator_id": "usr_admin_01", "reason": "Security testing kill switch"},
        )
        self.assert_test(
            "20. Trigger Emergency Kill Switch",
            st == 200 and b.get("paused") is True,
            f"({el:.1f}ms)",
            st,
            b,
        )

        # 21. Fail-closed during emergency pause (503 Service Unavailable)
        st, b, _, el = self._request(
            "POST",
            "/api/v1/actions",
            headers={"X-API-Key": self.agent_key, "X-Tenant-ID": self.tenant_id},
            data={
                "action_id": f"act_paused_{int(time.time() * 1000)}",
                "principal_id": "user_01",
                "agent_id": "agent_01",
                "resource_id": "prod_01",
                "variant_id": "var_01",
                "amount_minor": 129900,
                "currency": "INR",
            },
        )
        self.assert_test(
            "21. Kill Switch Fail-Closed (503)",
            st == 503 and b.get("error", {}).get("code") == "EMERGENCY_PAUSE_ACTIVE",
            f"({el:.1f}ms)",
            st,
            b,
        )

        # 22. Emergency Kill Switch resume
        st, b, _, el = self._request(
            "POST",
            "/api/v1/emergency/resume",
            headers={"Authorization": f"Bearer {self.admin_token}", "X-Tenant-ID": self.tenant_id},
            data={"operator_id": "usr_admin_01"},
        )
        self.assert_test(
            "22. Resume Normal Operations",
            st == 200 and b.get("paused") is False,
            f"({el:.1f}ms)",
            st,
            b,
        )

        # 23. Signed payment webhook ingestion
        wh_event_id = f"evt_live_{int(time.time() * 1000)}"
        wh_ts = str(int(time.time()))
        wh_body = {
            "event_id": wh_event_id,
            "event_type": "payment.captured",
            "provider": "payment",
            "payment_id": created_payment_id or "att_seeded_01",
            "provider_payment_id": "ch_mock_live_999",
            "tenant_id": self.tenant_id,
            "amount_minor": 129900,
            "currency": "INR",
            "status": "CAPTURED",
        }
        wh_raw = json.dumps(wh_body, separators=(",", ":"), sort_keys=True)
        canonical = f"{wh_ts}.{wh_raw}"
        sig = hmac.new(
            self.webhook_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        st, b, _, el = self._request(
            "POST",
            "/api/v1/webhooks/payments",
            headers={
                "x-webhook-signature": f"v1={sig}",
                "x-webhook-timestamp": wh_ts,
                "x-tenant-id": self.tenant_id,
            },
            data=wh_body,
        )
        self.assert_test(
            "23. HMAC-SHA256 Webhook Ingestion",
            st == 200 and b.get("status") in ("CAPTURED", "RECONCILED", "SUCCESS", "PROCESSED"),
            f"(status: {b.get('status')}, {el:.1f}ms)",
            st,
            b,
        )

        # 24. Webhook replay rejection
        st, b, _, el = self._request(
            "POST",
            "/api/v1/webhooks/payments",
            headers={
                "x-webhook-signature": f"v1={sig}",
                "x-webhook-timestamp": wh_ts,
                "x-tenant-id": self.tenant_id,
            },
            data=wh_body,
        )
        self.assert_test(
            "24. Anti-Replay Webhook Defense",
            st == 409 and b.get("error", {}).get("code") == "WEBHOOK_REPLAY_DETECTED",
            f"({el:.1f}ms)",
            st,
            b,
        )

        # 25. Forged webhook signature rejection
        st, b, _, el = self._request(
            "POST",
            "/api/v1/webhooks/payments",
            headers={
                "x-webhook-signature": "v1=0000000000000000000000000000000000000000000000000000000000000000",
                "x-webhook-timestamp": wh_ts,
                "x-tenant-id": self.tenant_id,
            },
            data=wh_body,
        )
        self.assert_test(
            "25. Tampered Webhook Signature Rejection",
            st == 401 and b.get("error", {}).get("code") == "INVALID_WEBHOOK_SIGNATURE",
            f"({el:.1f}ms)",
            st,
            b,
        )

        # 26. ACP External Protocol checkout
        st, b, _, el = self._request(
            "POST",
            "/api/v1/protocols/acp/checkout",
            headers={"X-API-Key": self.agent_key, "X-Tenant-ID": self.tenant_id},
            data={
                "acp_version": "1.0",
                "request_id": f"acp_req_{int(time.time() * 1000)}",
                "timestamp": datetime.now(UTC).isoformat(),
                "buyer": {
                    "principal_id": "user_01",
                    "agent_id": "agent_01",
                    "tenant_id": self.tenant_id,
                },
                "cart": {
                    "items": [
                        {
                            "product_id": "prod_01",
                            "variant_id": "var_01",
                            "name": "Pro Laptop",
                            "sku": "SKU-PRO-LAPTOP-16",
                            "quantity": 1,
                            "unit_price_minor": 129900,
                            "currency": "INR",
                        }
                    ],
                    "currency": "INR",
                    "total_minor": 129900,
                },
                "context": {"budget_max_minor": 200000},
            },
        )
        self.assert_test(
            "26. ACP Protocol Adapter Checkout",
            st == 200 and b.get("status") == "COMPLETED",
            f"({el:.1f}ms)",
            st,
            b,
        )

        log_line("-" * 80)
        log_line(f"FINAL RESULT: {self.tests_passed} passed, {self.tests_failed} failed.")
        log_line("=" * 80)
        return 0 if self.tests_failed == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentPay Live HTTP API Test Harness")
    parser.add_argument(
        "--url", default="", help="Target AgentPay base URL (e.g. http://127.0.0.1:8000)"
    )
    args = parser.parse_args()

    server = None
    thread = None
    base_url = args.url

    if not base_url:
        log_line(
            "[*] No target URL specified; bootstrapping live in-process server on ephemeral TCP socket..."
        )
        app = create_app(seed_demo_data=True, webhook_secret="whsec_live_test_secret_32bytes_len")
        server = create_server(host="127.0.0.1", port=0, app=app)
        addr = server.server_address
        host = str(addr[0])
        port = int(addr[1])

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.05)
        base_url = f"http://{host}:{port}"
        log_line(f"[+] Ephemeral server running on {base_url}")

    tester = LiveApiTester(base_url=base_url)
    exit_code = 1
    try:
        exit_code = tester.run_all_tests()
    finally:
        if server is not None:
            log_line("[*] Shutting down ephemeral server socket...")
            server.shutdown()
            server.server_close()
            if thread is not None:
                thread.join(timeout=2.0)
            log_line("[+] Server cleanly terminated.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
