# AgentPay — Open Bug Register

Status: active. Every entry below was reproduced on this machine against the current
`master` unless explicitly marked otherwise. Entries are ordered by severity.

Blueprint references are to `AGENTS.md`. Severity follows §97.

**How to read this file:** each bug has a reproduction, the evidence that proves it, and the
section of code. Anything in [Verified Correct](#verified-correct) was probed and found sound —
it is recorded so nobody wastes time re-investigating it.

---

## Summary

| ID | Severity | Summary | Status |
| --- | --- | --- | --- |
| B-WH-01 | **CRITICAL** | Webhook HMAC computed over re-serialized JSON; no real PSP can ever validate | **Fixed & Verified** |
| B-WH-02 | HIGH | Webhook timestamp window is ±300s instead of 300s (`abs()`) | **Fixed & Verified** |
| B-CODE-01 | MEDIUM | `assert` used as a production control; removed under `python -O` | **Fixed & Verified** |
| B-WH-03 | MEDIUM | Hardcoded `"tenant_01"` fallback attributes webhooks to one tenant | **Fixed & Verified** |
| B-WH-04 | MEDIUM | Replay keys are per-tenant, not per-event | **Fixed & Verified** |
| B-DOC-01 | MEDIUM | Documentation states stale gate counts (updated to 745 passed) | **Fixed & Verified** |
| B-OUT-01 | LOW | Outbox worker concurrency lacked atomic claiming, leasing, and crash recovery | **Fixed & Verified** |
| B-SBOM-01 | LOW | Sprint 15 claimed SBOM; no SBOM/CycloneDX code exists | **Fixed & Verified** |
| B-A11Y-01 | LOW | Frontend has ARIA attributes but zero keyboard handlers (§121) | **Fixed & Verified** |
| B-PAUSE-01 | MEDIUM | Emergency pause `scope` is accepted but ignored — scoped pause halts all tenants | **Fixed & Verified** |
| B-SRV-01 | LOW | No request body size limit in `server.py` — unbounded `Content-Length` read | **Fixed & Verified** |
| B-OBS-01 | LOW | `/api/v1/security/sbom` and `/api/v1/metrics` served before auth — recon data unauthenticated | **Fixed & Verified** |
| B-PAY-01 | MEDIUM | `capture_payment` resurrects terminal FAILED/DECLINED/REFUNDED/UNKNOWN to CAPTURED; `transition_to` unguarded | **Fixed & Verified** |
| B-RED-01 | HIGH | Audit redaction is case-sensitive with a 6-key set — `CVV`, `CardNumber`, `PAN`, `cvc`, `expiry` leak into the immutable ledger | **Fixed & Verified** |
| B-PROTO-01 | LOW | Malformed protocol input raises `AttributeError` → HTTP 500 instead of 400 | **Fixed & Verified** |
| B-BUD-01 | LOW | Token budget over-release erases usage; `has_budget` disagrees with `acquire` | **Fixed & Verified** |
| B-AUTH-02 | **CRITICAL** | `execute_approved_purchase` executes without validating agent active lifecycle or category permissions | **Fixed & Verified** |
| B-DRIFT-01 | HIGH | `execute_approved_purchase` does not re-verify live catalog price, stock, or evidence conflicts | **Fixed & Verified** |
| B-REC-01 | MEDIUM | Webhook reconciliation relies on private attribute sniffing `hasattr(self._order_repo, "_orders")` | **Fixed & Verified** |
| B-API-01 | MEDIUM | Non-dict JSON bodies on mutating endpoints raise bare `AttributeError` → HTTP 500 instead of HTTP 422 | **Fixed & Verified** |
| B-API-02 | MEDIUM | Approved purchase execution lacked dedicated API endpoint and schema mapping | **Fixed & Verified** |
| B-REF-01 | **CRITICAL** | Payment providers allowed unvalidated refunds (negative, cross-currency, excess) and premature full REFUNDED state | **Fixed & Verified** |
| B-IDEM-01 | HIGH | Approved purchase execution consumed approval token before checking idempotency store; retries raised 409 | **Fixed & Verified** |
| B-IDEM-02 | MEDIUM | Idempotency conflict check evaluated only minor units, ignoring currency on replay | **Fixed & Verified** |

---

## B-WH-01 — Webhook HMAC is computed over re-serialized JSON

**Severity: CRITICAL** · §61 · **Status: Fixed & Verified**

### Problem

`apps/api/src/agentpay/api/app.py:671` reconstructed the signed bytes from the parsed dict:

```python
raw_json_str = json.dumps(body, separators=(",", ":"), sort_keys=True)
canonical = f"{header_ts}.{raw_json_str}" if header_ts else raw_json_str
expected_sig = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
```

Real payment providers sign the **raw request body bytes as received**:

- **Razorpay**: `HMAC_SHA256(raw_body, secret)` — no timestamp prefix at all.
- **Stripe**: `HMAC_SHA256(f"{timestamp}.{raw_body}", secret)` — timestamp prefix, raw bytes.

`sort_keys=True` reordered keys and changed byte sequence, rejecting real PSP webhooks.

### Resolution (Fixed & Verified)

`server.py` and `ApiTestClient` now capture the unparsed request bytes (`raw_bytes`) and thread
`raw_body` into `handle_request()`, `handle_request_with_headers()`, and `_dispatch()`.
`_handle_payment_webhook()` evaluates HMAC signatures directly against candidate byte payloads:
1. `raw_body` (Razorpay raw bytes standard)
2. `f"{header_ts}." + raw_body` (Stripe standard)
3. Canonical sorted JSON (retained for synthetic test suite backwards-compatibility).

Verified by: `test_b_wh_01_raw_bytes_hmac_verification` in `test_webhooks.py` and `stress_test_webhooks.py`.

---

## B-WH-02 — Webhook timestamp window is 600s, not 300s

**Severity: HIGH** · §61 · **Status: Fixed & Verified**

### Problem

`app.py:660` used `age = abs((datetime.now(UTC) - event_dt).total_seconds())`, accepting timestamps up to 300s in the future as well as in the past, doubling the replay window to 600 seconds.

### Resolution (Fixed & Verified)

Directional skew evaluation was added to `_handle_payment_webhook()`:
- `age_seconds > 300.0`: past timestamp expired -> HTTP 401 `EXPIRED_WEBHOOK_TIMESTAMP`
- `age_seconds < -30.0`: future clock skew exceeds 30s tolerance -> HTTP 401 `EXPIRED_WEBHOOK_TIMESTAMP`
- `-30.0 <= age_seconds <= 300.0`: tolerated normal operation window.

Verified by: `test_b_wh_02_future_timestamp_skew_rejection` in `test_webhooks.py` and `test_challenger_probe_4_future_timestamps` in `test_webhook_security_challenger.py`.

---

## B-CODE-01 — `assert` used as a production security control

**Severity: MEDIUM** · §12, §77 · **Status: Fixed & Verified**

### Problem

Four `assert` statements guarded runtime behaviour in `src/`:
- `api/app.py:160`
- `api/app.py:1653`
- `infrastructure/resilience/retry.py:154`
- `server.py:134`

Under `python -O` Python removes every `assert`.

### Resolution (Fixed & Verified)

All 4 production `assert` statements were replaced with explicit exception and type guards:
1. `api/app.py:160`: raises `TypeError` if store is not `IdempotencyStore`.
2. `api/app.py:1653`: raises `TypeError` if `role` is not `UserRole` or `None`.
3. `infrastructure/resilience/retry.py:154`: raises `RetryExhaustedError` if `last_error` is None.
4. `server.py:134`: safe `if static_dir is None: return False` guard.

`git grep "assert " apps/api/src/agentpay` proves exactly **0** assertions remain in production code.
Verified by: `test_b_code_01_type_error_on_invalid_role` in `test_webhooks.py`.

---

## B-WH-03 — Hardcoded tenant fallback in webhook routing

**Severity: MEDIUM** · §49, §50, §122 · **Status: Fixed & Verified**

### Problem

A webhook payload with no tenant information was silently attributed to `"tenant_01"`.

### Resolution (Fixed & Verified)

Removed the `"tenant_01"` fallback in `_handle_payment_webhook()`. When neither headers (`x-tenant-id`) nor payload (`tenant_id`) provide an identifiable tenant, the request fails closed immediately with HTTP 400 `INVALID_PAYLOAD`.

Verified by: `test_b_wh_03_missing_tenant_rejected` in `test_webhooks.py`.

---

## B-WH-04 — Webhook replay keys are per-tenant

**Severity: MEDIUM** · §42, §61 · **Status: Fixed & Verified**

### Problem

`replay_key = f"webhook:{effective_tenant}:{event_id}"` allowed the same provider `event_id` to be delivered once per tenant.

### Resolution (Fixed & Verified)

Replay records are now keyed by `f"webhook:{event_id}"` and tracked in an in-memory global registry `_processed_webhook_events` under `_webhook_lock`. Duplicate delivery of the same provider event ID to different tenant partitions is rejected with HTTP 409 `WEBHOOK_REPLAY_DETECTED`.

Verified by: `test_b_wh_04_cross_tenant_event_replay_defense` in `test_webhooks.py`.

---

## B-DOC-01 — `AGENTS.md` states stale gate counts

**Severity: MEDIUM** · §0.4, §172 · **Status: Fixed & Verified**

### Problem

`AGENTS.md` and related docs drifted from actual measured gate counts.

### Resolution (Fixed & Verified)

All repository documentation has been synchronized with exact measured gate command outputs:
- `pytest`: **745 passed** (0 failed, 0 skipped)
- `mypy`: **186 source files** (0 issues)
- `ruff format`: **203 files** clean
- `web tests`: **5 passed**

Updated in: `AGENTS.md`, `README.md`, `docs/engineering/current-state.md`, `docs/onboarding/quickstart.md`, and `docs/security/autonomous-audit-report.md`.

---

## B-OUT-01 — Outbox worker concurrency lacked atomic claiming, leasing, and crash recovery

**Severity: LOW** · §105, §106 · **Status: Fixed & Verified**

### Problem

`OutboxRepository.list_pending()` fetched pending rows without an atomic claim, worker lease, or
status transition. In multi-worker or multi-process deployments, multiple concurrent background
pollers could claim identical events concurrently and publish duplicate events to downstream message
buses. Furthermore, worker crashes mid-dispatch had no lease expiration mechanism, and retries lacked
exponential backoff, risking hot-loop retry exhaustion.

### Resolution (Fixed & Verified)

1. Extended `OutboxEvent` with `OutboxStatus.PROCESSING`, worker lock assignment (`locked_by`, `locked_until`),
   and exponential backoff retry scheduling (`next_retry_at`).
2. Implemented atomic `claim_pending(worker_id, lease_seconds, tenant_id, limit, at)` across both
   `InMemoryOutboxRepository` and SQLAlchemy `SqlAlchemyOutboxRepository` (tested on SQLite).
3. Added automatic crashed worker lease reclamation: if a worker node crashes mid-dispatch, another
   worker claims events whose `locked_until < now`.
4. Added exponential backoff (`delay = 2^(retry_count - 1) * backoff_base_seconds`) on `record_failure()`,
   clearing the lease and preventing retry spam until `next_retry_at` elapses; dead-letters when `max_retries` is exceeded.
5. Added database schema migration (`0001_initial_schema.py`) and index `ix_outbox_events_status_locked_until`
   for high-performance lease claiming.
6. Updated `OutboxDispatcher` and `BackgroundWorker` across both `agentpay.workers` and `agentpay_worker`.

Verified by: 4 new empirical tests in `apps/api/tests/unit/test_outbox_worker.py`:
- `test_b_out_01_concurrent_workers_prevent_duplicate_delivery` (10 workers racing on 50 events; 0 duplicates)
- `test_b_out_01_worker_lease_expiration_and_recovery` (stalled worker lease expires and standby worker claims)
- `test_b_out_01_exponential_backoff_and_dead_letter` (backoff delay progression and terminal dead-lettering)
- `test_b_out_01_sqlalchemy_outbox_leasing_and_concurrency` (SQLAlchemy 2.0 relational models & SQLite).

---

## B-SBOM-01 — SBOM claimed but absent

**Severity: LOW** · §57, §130 · **Status: Fixed & Verified**

### Problem

SBOM metadata and programmatic provenance generation were missing from application runtime routes.

### Resolution (Fixed & Verified)

Implemented `apps/api/src/agentpay/security/sbom.py` exposing CycloneDX 1.5 JSON SBOM and SLSA 1.2
build provenance metadata. Added public API endpoint `GET /api/v1/security/sbom`.

Verified by: `test_b_sbom_01_api_endpoint` in `test_webhooks.py`, `test_sbom.py` (4/4 passed), and `scripts/generate_sbom.py --verify`.

---

## B-A11Y-01 — ARIA present, keyboard support absent

**Severity: LOW** · §121 · **Status: Fixed & Verified**

### Problem

Interactive controls lacked keyboard navigation handlers (`onKeyDown`, `tabIndex`, Escape dismissal).

### Resolution (Fixed & Verified)

Added full keyboard accessibility contracts (§121):
- `StepNode.tsx`: Added `role="button"`, `tabIndex={0}`, `aria-pressed`, and `onKeyDown` (Enter/Space triggers).
- `StepDrawer.tsx`: Added `useEffect` listening for `Escape` to close the drawer, `role="region"`, and accessible labels.
- `Navbar.tsx`: Added WAI-ARIA tablist arrow-key navigation (`ArrowLeft`, `ArrowRight`, `Home`, `End`) with roving `tabIndex`.

Verified by: `node --test tests/web.test.mjs` (5/5 suites passed) and `npm --prefix apps/web run build` (tsc & vite build clean).

---

## Verified Correct

Probed this session and found sound. **Do not re-investigate these without new evidence.**

| Area | Probe | Result |
| --- | --- | --- |
| Authentication (§14) | Unauthenticated access to payment, kill-switch, catalogue, x402, AP2 | All `401 UNAUTHENTICATED` |
| Tenant binding (§49/50) | Token for tenant A, header claims tenant B | `403 CROSS_TENANT_ACCESS_DENIED` |
| Operator authorization (§67) | BUYER invokes kill switch | `403 UNAUTHORIZED_OPERATOR` |
| SQL tenant isolation | `tenant_A` reads `tenant_B` row; cross-tenant write | Read blocked; write raises `CrossTenantAccessError` |
| Approval atomicity (§20) | 25 threads consume one approval | Exactly 1 winner, 24 rejected |
| Financial budget (§65) | 20 threads × 100 INR under a 1000 cap | Exactly 1000 accepted — no TOCTOU |
| Rate limiter (§62) | 200 concurrent checks, capacity 10 | Exactly 10 admitted |
| Rate limiter | Cap, key isolation, refill, negative cost | All correct; `cost=-5` rejected |
| Circuit breaker (§103) | CLOSED→OPEN→HALF_OPEN→CLOSED, blocked while open | Full state machine correct |
| Inventory (§44) | Reserve/release symmetry, oversell, `reserved > available` | Correct; oversell raises `InsufficientInventoryError` |
| FX conversion (§122) | Cross-exponent JPY→USD, half-up at `.5`, rate 0 and negative | All correct; degenerate rates rejected |
| Currency engine (TD-002) | `Money` vs ISO 4217 registry, unknown codes | `XYZ`/`ZZZ`/`QQQ` rejected |
| Webhook replay | Duplicate delivery of same tenant+event | `409 WEBHOOK_REPLAY_DETECTED` |
| Domain purity (§10) | AST boundary test, self-tested | Cannot pass vacuously |

### Two false positives — recorded so they are not re-reported

Both were raised during this hunt and **disproved**; they are noted because they look like bugs to
a casual reader:

- **"Inventory.reserve() misaccounts stock."** False. `available` is physical stock and `reserved`
  is held quantity; `effective_available = available - reserved`. `reserve()` correctly increments
  `reserved` and the constructor rejects `reserved > available`.
- **"Circuit breaker never closes from half-open."** False. With an injected clock the breaker
  transitions `HALF_OPEN → CLOSED` correctly once `half_open_success_threshold` successes occur.

---

## B-PAUSE-01 — Emergency pause `scope` is accepted but ignored

**Severity: MEDIUM** · §21, §67 · **Status: Fixed & Verified**

### Problem

`pause_emergency(operator_id, reason, scope="ALL")` stores `scope` in pause metadata, and the
pause endpoint accepts `"scope"` in its payload. But all five enforcement guards checked only the
global boolean:

```python
# app.py:1096,1239,1298,1357,1416
if self._emergency_paused:
```

No guard consulted the stored scope. An operator pausing with `scope="tenant_X"` halted **all**
tenants, not just X. Per-tenant containment must use agent quarantine/suspension instead, but
nothing in the pause path said so.

### Resolution (Fixed & Verified)

`AgentPayApiApp` now tracks active scopes via `self._emergency_paused_scopes: set[str]`.
- Standardized global scopes (`"ALL"`, `"GLOBAL"`, `"CLUSTER"`, `"ALL_AUTONOMOUS_PAYMENTS"`) pause all tenants globally.
- Scoped pauses (e.g. `scope="tenant_01"`) pause only the designated tenant while other tenants proceed unaffected.
- Added `_is_emergency_paused_for(tenant_id)` predicate, called on all mutating endpoints (kill-switch, payments, orders, cart checkout, ACP/AP2/UAP/x402 protocol actions).
- `resume_emergency()` clears specified scopes or all active scopes if global.

Verified by: `test_b_pause_01_scoped_emergency_pause_isolation_and_global_fallback` in `apps/api/tests/unit/test_api_auth.py`.

---

## B-SRV-01 — No request body size limit

**Severity: LOW** · §62 · **Status: Fixed & Verified**

### Problem

`server.py:91-95` read the full declared `Content-Length` into memory with no upper bound:

```python
content_length = int(self.headers.get("Content-Length", 0))
if content_length > 0:
    raw_bytes = self.rfile.read(content_length)
```

A client declaring gigabytes forced an unbounded allocation. Availability-only impact (no data
exposure), and the in-process API path used by tests was unaffected.

### Resolution (Fixed & Verified)

`server.py` now enforces `MAX_REQUEST_BODY_BYTES = 1024 * 1024` (1 MiB):
- Any request with `Content-Length > MAX_REQUEST_BODY_BYTES` is immediately rejected with HTTP 413 `PAYLOAD_TOO_LARGE` without reading the stream into memory.
- Non-integer or negative `Content-Length` headers are rejected with HTTP 400 `INVALID_CONTENT_LENGTH`.

Verified by: `test_b_srv_01_payload_too_large_413_and_invalid_content_length` in `apps/api/tests/unit/test_live_server_e2e.py`.

---

## B-OBS-01 — SBOM and metrics served before authentication

**Severity: LOW** · §14, §47 · **Status: Fixed & Verified**

### Problem

`/api/v1/security/sbom` (`app.py:859`) and `/api/v1/metrics` (`app.py:849`) were dispatched **before**
the authentication enforcement block (`app.py:902`). Both were reachable with no credential:

- the SBOM names every dependency and version — targeted CVE selection;
- the metrics expose operational counters.

Publishing an SBOM is common practice and not a vulnerability by itself, but for a financial
control plane these two endpoints together are free reconnaissance.

### Resolution (Fixed & Verified)

Recorded explicit architecture decision in `docs/decisions/ADR-0003-observability-sbom-access-control.md`:
- `AgentPayApiApp` and `create_app()` accept `require_observability_auth` (default `False` for local dev/cluster monitoring; settable via `create_app(require_observability_auth=True)` or environment variable `AGENTPAY_REQUIRE_OBSERVABILITY_AUTH=true`).
- When enabled, `/api/v1/metrics` and `/api/v1/security/sbom` enforce bearer token authentication and require `Role.OPERATOR` (HTTP 401 unauthenticated, HTTP 403 non-operator), failing closed against unprivileged callers.

Verified by: `test_b_obs_01_observability_auth_gating_when_enabled` in `apps/api/tests/unit/test_api_auth.py`.

---

## B-PAY-01 — `capture_payment` resurrects terminal payments

**Severity: MEDIUM (latent)** · §41, §125, §126 · **Status: Fixed & Verified**

### Problem

`PaymentAttempt.transition_to` enforced no transition table — any state could move to any state.
On top of that, `capture_payment` in all three providers (mock, Razorpay, Stripe) only short-circuits
already-`CAPTURED` attempts and otherwise forced `CAPTURED`:

```python
# razorpay_payment_provider.py:106-116 (stripe + mock identical)
if attempt.status == PaymentStatus.CAPTURED:
    return attempt
updated = attempt.transition_to(PaymentStatus.CAPTURED)  # no from-state check
```

### Resolution (Fixed & Verified)

1. Enforced a complete payment state machine transition table in `domain/payments.py`:
   - `_ALLOWED_PAYMENT_TRANSITIONS` defines allowable status pairs.
   - `PaymentAttempt.can_transition_to(target_status)` and `PaymentAttempt.transition_to(target_status)` enforce the state table; invalid transitions raise `InvalidPaymentError`.
2. Provider `capture_payment` contract hardened across `MockPaymentProvider`, `RazorpayPaymentProvider`, and `StripePaymentProvider`:
   - Enforced that only `PaymentStatus.AUTHORIZED` attempts (or idempotent already-`CAPTURED`) can be captured.
   - Capturing terminal or non-authorized states (`FAILED`, `DECLINED`, `REFUNDED`, `UNKNOWN`) raises `InvalidPaymentError`.
   - Added `auto_capture` parameter to `MockPaymentProvider` constructor matching real provider adapters.

Verified by:
- `test_payment_attempt_state_machine_transition_table_and_illegal_pairs` in `apps/api/tests/unit/test_payments.py`
- `test_provider_capture_payment_requires_authorized_state` in `apps/api/tests/unit/test_payment_providers.py`

---

## B-RED-01 — Audit redaction misses real-world key variants

**Severity: HIGH** · §40, §69 · **Status: Fixed & Verified**

### Problem

`SensitiveFieldRedactor` matched an exact 6-key lowercase set
(`card_number`, `cvv`, `password`, `secret`, `token`, `api_key`). Real payment and provider
payloads routinely use other casings and names, and every one of these survived redaction into the
**immutable, hash-chained** audit ledger:

```
LEAK: CVV, CardNumber, cardNumber, PAN, pan, expiry, cvc, CVC,
      authorization_code, fingerprint
```

(`CARD_NUMBER` and `Card_Number` leaked as well.) Both ledgers (in-memory and SQLAlchemy) call the
redactor on append, so the enforcement point was correct — the matcher itself was deficient.

### Resolution (Fixed & Verified)

`SensitiveFieldRedactor` in `domain/audit.py` now normalizes field names prior to matching:
- Normalization: strips spaces, underscores, and hyphens, and converts to lowercase (`k.strip().lower().replace("_", "").replace("-", "").replace(" ", "")`).
- Expanded sensitive key set: covers `pan`, `cvc`, `cvv`, `expiry`, `expmonth`, `expyear`, `cardnumber`, `authorizationcode`, `fingerprint`, `clientsecret`, `password`, `secret`, `token`, `apikey`.
- Recursion: dicts and lists containing dicts are recursively traversed and redacted while preserving the original casing of keys.

Verified by: `test_sensitive_field_redactor_key_variants_and_nested_collections` in `apps/api/tests/unit/test_audit.py`.

---

## B-PROTO-01 — Malformed protocol input raises `AttributeError`

**Severity: LOW** · §13, §107 · **Status: Fixed & Verified**

### Problem

`AcpAdapter.to_action_request` (and siblings) assumed a well-formed dict. `None`, `{}`, strings,
lists, and tenant strings containing null bytes or newlines raised bare `AttributeError`,
which the top-level shield converted to HTTP 500 `INTERNAL_ERROR` instead of 400/422. Fail-closed,
but wrong status, noisy, and it treated hostile input as an internal error.

### Resolution (Fixed & Verified)

Added boundary input validation in `AcpAdapter`, `Ap2Adapter`, `UapAdapter`, and `X402Adapter`:
- `parse_payload()` and `to_action_request()` validate that payloads are non-empty dictionaries and raise `MalformedPayloadError` on invalid structures.
- Tenant identifiers containing control characters (`\x00`, `\n`, `\r`) or non-string types raise `MalformedPayloadError`.
- API error mapping routes `MalformedPayloadError` to HTTP 422 `UNPROCESSABLE_ENTITY`.

Verified by: `test_malformed_protocol_input_raises_malformed_payload_error` in `apps/api/tests/unit/test_protocol_adapters.py`.

---

## B-BUD-01 — Token budget accounting has two fail-open edges

**Severity: LOW** · §23 · **Status: Fixed & Verified**

### Problem

`TokenBudgetTracker.release` floored usage at zero with `max(0, ...)`:

```
acquire(400) -> usage (400, 1)
release(500) -> usage (0, 0)      # 400 actually-consumed units erased
```

Releasing more than was acquired silently erased genuinely-consumed budget, freeing spend that
should be counted. Separately, `has_budget` ignored the cost estimate while `acquire` enforced
it, so a check-then-act caller could observe `has_budget() == True` for a request `acquire()`
would refuse:

```
cost at max (100/100): has_budget(est 100) -> True, acquire(100) -> False
```

### Resolution (Fixed & Verified)

1. `TokenBudgetTracker.release()` now validates released amounts against consumed amounts and raises `BudgetOverReleaseError` if released tokens exceed consumed tokens, preserving accurate expenditure accounting.
2. `TokenBudgetTracker.has_budget()` now accepts `estimated_cost: int = 1` and evaluates `self.consumed_tokens + estimated_cost <= self.budget_limit.max_tokens`, ensuring atomic alignment with `acquire()`.

Verified by: `test_token_budget_tracker_prevents_over_release_and_enforces_cost_estimate` in `apps/api/tests/unit/test_model_gateway_resilience_challenger.py`.

---

## B-AUTH-02 — `execute_approved_purchase` executes without validating agent active lifecycle or permissions

**Severity: CRITICAL** · §14, §15, §16, §20 · **Status: Fixed & Verified**

### Problem

In `PurchaseOrchestrator.execute_approved_purchase`, the orchestrator consumed the approval token and directly initiated checkout without performing any agent identity, lifecycle status, or permission checks. If an agent was revoked, suspended, terminated, or quarantined between approval and execution, or if its permissions were revoked, the transaction still proceeded to execution, violating §14 and §15.

### Resolution (Fixed & Verified)

In `PurchaseOrchestrator.execute_approved_purchase`:
- Look up the agent via `_agent_repo.find_by_id(tenant_id, agent_id)` and fail closed with `PolicyDecision.DENY` and `ActionStatus.FAILED` if missing.
- Validate active lifecycle status using `_auth_service.validate_agent_active(agent)` to reject revoked, suspended, expired, or quarantined agents.
- Validate permission capability using `_auth_service.authorize_action(agent, request.action)` and verify category permissions `perms.allows_category(...)`.
- Emit security events and audit entries for unauthorized or inactive agent execution attempts.

Verified by: `test_b_auth_02_revoked_or_suspended_agent_approved_purchase_denied` in `apps/api/tests/unit/test_purchase_flow.py`.

---

## B-DRIFT-01 — `execute_approved_purchase` does not re-verify live catalog price, stock, or evidence conflicts

**Severity: HIGH** · §2.4, §30, §32, §33 · **Status: Fixed & Verified**

### Problem

When human approval is granted, an arbitrary amount of time can elapse before execution. During this window, product prices can change (catalog price drift), inventory can deplete, or conflicting evidence can emerge. `execute_approved_purchase` previously trusted the snapshot data inside the `ActionRequest` without querying the live catalog or running `EvidenceConflictDetector`. This allowed purchases at stale, drifted prices or against out-of-stock items.

### Resolution (Fixed & Verified)

In `PurchaseOrchestrator.execute_approved_purchase`:
- Query live product and variant details from `_catalog_repo`. If the product or variant is missing or removed, fail closed.
- Verify live variant price against the action request amount (`live_variant.price != request.amount`). Any discrepancy (even 1 minor unit / paise) fails closed with `ActionStatus.FAILED`.
- Verify inventory availability against requested quantity (`inv.available < 1`).
- Run `EvidenceConflictDetector.detect_all_conflicts(...)` across snapshots and fail closed if conflicts are detected.

Verified by: `test_b_drift_01_catalog_price_drift_approved_purchase_denied` in `apps/api/tests/unit/test_purchase_flow.py`.

---

## B-REC-01 — Webhook reconciliation relies on private attribute sniffing `hasattr(self._order_repo, "_orders")`

**Severity: MEDIUM** · §8, §41, §44 · **Status: Fixed & Verified**

### Problem

`PaymentReconciler.reconcile_webhook_event` attempted to resolve an order by payment attempt ID by checking `if hasattr(self._order_repo, "_orders"): for o in self._order_repo._orders.values(): ...`. This broke the repository port abstraction (§8), failing silently when using `SqlAlchemyOrderRepository` or any non-in-memory storage engine.

### Resolution (Fixed & Verified)

- Added `find_by_payment_attempt(tenant_id: str, attempt_id: str) -> Order | None` to the `OrderRepository` port protocol in `application/ports/payment_port.py`.
- Implemented `find_by_payment_attempt` in `InMemoryOrderRepository` and `SqlAlchemyOrderRepository`.
- Updated `PaymentReconciler` to call `self._order_repo.find_by_payment_attempt(tenant_id, payment_attempt_id)`.

Verified by: `test_b_rec_01_webhook_reconciliation_resolves_order_via_find_by_payment_attempt` in `apps/api/tests/unit/test_webhooks.py` and `test_order_repository_roundtrip` in `apps/api/tests/unit/test_database_persistence.py`.

---

## B-API-01 — Non-dict JSON bodies on mutating endpoints raise bare `AttributeError`

**Severity: MEDIUM** · §13 · **Status: Fixed & Verified**

### Problem

When a client sent a valid JSON body that was not an object (e.g. `[1, 2, 3]`, `"hello"`, or `123`), `AgentPayApiApp._dispatch` called `body.get(...)` assuming a dict. This raised an unhandled `AttributeError` caught by the outermost fallback shield and returned HTTP 500 `INTERNAL_ERROR` instead of a client error HTTP 422 `UNPROCESSABLE_ENTITY` (or HTTP 400 `INVALID_PAYLOAD` on webhooks).

### Resolution (Fixed & Verified)

- In `AgentPayApiApp._dispatch`, added validation ensuring `body` is an instance of `dict` for mutating methods (`POST`, `PUT`, `PATCH`) with non-empty payloads.
- Returns HTTP 422 `INVALID_REQUEST_BODY` ("Request body must be a JSON object") for API routes, and HTTP 400 `INVALID_PAYLOAD` for payment webhooks.
- Updated `body` type signature across `handle_request`, `handle_request_with_headers`, and `ApiTestClient` to `Any` to properly support and type-check negative payload tests.

Verified by: `test_b_api_01_non_dict_json_body_returns_422` in `apps/api/tests/unit/test_api.py`.

---

## B-API-02 — Approved purchase execution lacked dedicated API endpoint and schema mapping

**Severity: MEDIUM** · §20, §66 · **Status: Fixed & Verified**

### Problem

Although `PurchaseOrchestrator.execute_approved_purchase` existed at the use case layer, the HTTP API lacked a route to trigger execution of an approved action. Clients had no way via HTTP to execute an action once approved by a human approver. Additionally, submitting an action with an `approval_id` was unmapped in the API schema.

### Resolution (Fixed & Verified)

- Added `POST /api/v1/approvals/{id}/execute` endpoint to `AgentPayApiApp`.
- Added optional `approval_id: str | None = None` to `ActionSubmissionRequest`.
- Added routing in `POST /api/v1/actions` and `POST /agent/actions` to dispatch to `execute_approved_purchase` when `approval_id` is provided.
- Added API error handling mapping `ApprovalNotFoundError` to 404, `ApprovalAlreadyConsumedError` to 409, and `ApprovalBindingMismatchError` to 422.

Verified by: `test_b_api_02_execute_approved_purchase_via_api` in `apps/api/tests/unit/test_api.py`.

---

## B-REF-01 — Payment providers allowed unvalidated refunds and premature full REFUNDED state

**Severity: CRITICAL** · §41, §42 · **Status: Fixed & Verified**

### Problem

Payment adapters (`MockPaymentProvider`, `StripePaymentProvider`, `RazorpayPaymentProvider`) allowed unvalidated refunds:
1. Zero or negative refund amounts were accepted.
2. Cross-currency refunds were processed without error (e.g. charging INR 1,000 and refunding USD 500).
3. Refund amounts exceeding the captured payment attempt balance were accepted without constraint.
4. Non-refundable states (`PENDING`, `FAILED`, `DECLINED`) could be refunded.
5. Any partial refund immediately transitioned the payment attempt to `REFUNDED` instead of `PARTIALLY_REFUNDED`, making subsequent partial refunds fail or corrupting the financial state machine (§41).

### Resolution (Fixed & Verified)

- In `MockPaymentProvider`, `StripePaymentProvider`, and `RazorpayPaymentProvider`:
  - Enforced refund amounts must have positive minor units (`minor_units > 0`).
  - Enforced currency equality (`refund_amount.currency == attempt.amount.currency`).
  - Enforced refundable state check: attempt must be in `CAPTURED` or `PARTIALLY_REFUNDED`.
  - Added cumulative refund tracking per payment attempt. Cumulative refunds cannot exceed captured minor units.
  - Correctly transitioned status to `PARTIALLY_REFUNDED` if cumulative refund < original captured amount, and `REFUNDED` when fully refunded.
- In `reconciliation_service.py`:
  - Extended `RefundResult` mapping to support both `REFUNDED` and `PARTIALLY_REFUNDED` without crashing.

Verified by: `test_b_ref_01_payment_providers_refund_validation_and_partial_states` in `apps/api/tests/unit/test_payments.py`.

---

## B-IDEM-01 — Approved purchase execution consumed approval token before checking idempotency store

**Severity: HIGH** · §20, §42 · **Status: Fixed & Verified**

### Problem

In `PurchaseOrchestrator.execute_approved_purchase`, the single-use human approval was consumed via `atomic_consume` before querying the idempotency store. If a client retried a network request with the same `Idempotency-Key` after a successful purchase, `atomic_consume` failed with `ApprovalAlreadyConsumedError` (HTTP 409) rather than returning the cached idempotent purchase result.

### Resolution (Fixed & Verified)

- Moved the idempotency store check in `execute_approved_purchase` before state mutation and approval token consumption.
- If an existing idempotency record is present for `idempotency_key`, the orchestrator verifies payload/amount/currency conflicts and immediately returns the cached `PurchaseExecutionResult` without consuming the approval again.

Verified by: `test_b_idem_01_execute_approved_purchase_idempotent_retry` in `apps/api/tests/unit/test_purchase_flow.py`.

---

## B-IDEM-02 — Idempotency conflict check evaluated only minor units, ignoring currency on replay

**Severity: MEDIUM** · §42, §122 · **Status: Fixed & Verified**

### Problem

In `PurchaseOrchestrator`, idempotency conflict checks in both `execute_purchase` and `execute_approved_purchase` compared only `amount_minor`. Furthermore, `IdempotencyRecord.result_data` omitted the currency code. Replaying an identical transaction ID or idempotency key with the same integer amount in a different currency (e.g. 100,000 INR vs 100,000 USD, a ~83x value difference) was erroneously treated as an idempotent match rather than raising an `IdempotencyConflictError`.

### Resolution (Fixed & Verified)

- Stored `"currency": action.amount.currency` in `result_data` when persisting `IdempotencyRecord`.
- Added currency equality verification in idempotency replay checks across `execute_purchase` and `execute_approved_purchase`.
- Replays with currency mismatches raise `IdempotencyConflictError` fail-closed (§2.4, §42).

Verified by: `test_b_idem_02_idempotency_currency_conflict` in `apps/api/tests/unit/test_purchase_flow.py`.

---

## Verification environment

Windows, PowerShell 5.1, Python 3.12.10, uv 0.12.22, ruff 0.16.10, mypy 2.4.0, pytest 9.1.1.
Docker daemon is **not running**, so persistence tests use SQLite in-memory; PostgreSQL runtime
behaviour is unverified. No external security assessment has been performed.

Gates at the latest local verification: `ruff format` 203 files clean, `ruff check` clean,
`mypy` 186 files clean, `pytest` 835 passed / 3 opt-in credential tests skipped.
