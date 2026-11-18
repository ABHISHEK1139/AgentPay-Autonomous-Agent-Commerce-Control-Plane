# Security and readiness review — 2026-11-17

## Scope and evidence

This is an incremental internal review, not certification or an exhaustive security assessment. Existing uncommitted changes were preserved. Earlier conversation claims that the whole system was fully secure and ready were unsupported.

## Reproduced and fixed

- HTTP transport response headers: the threaded server discarded the returned ApiResponse headers and read shared application last_response_headers. Another request could replace those headers before serialization. The transport now uses the response snapshot. This fixes the transport window only; shared header construction inside the application still needs concurrency review.
- Browser idempotency requests: CORS Access-Control-Allow-Headers omitted Idempotency-Key. Browsers would reject a cross-origin request carrying that header. It is now allowed.

Regression evidence: both cases in apps/api/tests/unit/test_http_transport_regressions.py failed against the original implementation, then passed after the fixes. Together with test_live_server_e2e.py: 12 passed.

Full gates after the fixes: ruff format --check (217 files), ruff check clean, mypy clean (198 source files), pytest 954 passed and 3 opt-in live-provider tests skipped. The final server diff was reviewed. No external provider verification is implied by these results.

## Follow-up findings requiring investigation/remediation

A separate read-only source review identified:

1. Reproduced and fixed missing-column startup acceptance: a real SQLite database migrated to 0005 passed the old guard. The guard now validates all ORM mapped tables and columns; regression tests reject 0005 and accept head. Focused configuration/schema tests: 38 passed. Full gates: formatting 218 files, lint clean, mypy 199 files clean, pytest 956 passed / 3 opt-in skips. Applied revision identity and constraint/type drift remain outside this guard's checks.
2. Production-like configuration can choose in-memory state when database_url is absent. Remote sandbox effects can outlive local records after restart.
3. Cart checkout locks are process-local; known-limitations.md already records missing database-level checkout claims for multi-replica safety.
4. The health endpoint reports liveness without testing database/provider readiness; it should not be interpreted as readiness evidence.
5. Architecture and remediation-report completion claims exceed documented external integration evidence. Mock transport tests do not prove an external checkout/webhook/restart journey.

These follow-up findings are not fixes or independently reproduced runtime exploits in this pass. Production readiness remains unestablished. No production credentials or external payment operations were used.

## ASGI transport follow-up

Two additional transport regressions were reproduced: ASGI did not pass original request bytes to the dispatcher (breaking raw-byte webhook verification), and buffered oversized bodies before parsing without enforcing the 1 MiB limit. ASGI now forwards the exact bytes and rejects an over-limit cumulative body with HTTP 413 before JSON parsing or dispatch. Client disconnects stop processing. Four regression cases cover single-message and chunked bodies, both normal and oversized. Both original cases failed before the patch. The existing request-context regressions also passed against changes already present in the workspace when this round resumed.

Verification after the ASGI fixes: formatting 220 files clean, lint clean, mypy 201 files clean, pytest 976 passed / 3 opt-in live-provider skips. Reviewed the application diff while preserving pre-existing changes. No live provider was called in this round.

## Payment webhook follow-up

A payment review found two financial integrity defects. Refund webhook validation checked each event only against the original capture, so two distinct partial-refund event IDs could exceed the captured amount. The reconciler now sums persisted refunds for the tenant and payment attempt before accepting another refund; a regression test reproduces the former 60,000 + 80,000 over-refund and confirms the second event is rejected and the stored total remains 60,000.

Capture webhooks also transitioned an attempt to CAPTURED without comparing provider amount/currency to the authorized attempt. The reconciler now rejects non-integer, mismatched amount, or mismatched currency before state/order mutation. A regression test confirms a 1 USD capture cannot confirm a 100,000 INR attempt.

Focused payment regression evidence: 4 C6 tests passed. The first full-gate run exposed a compatibility issue in the new capture validation: existing structured `{minor_units, currency}` amount payloads were rejected. Normalization now handles that representation, and both durable SQLite webhook tests pass. Full gates are running again after this correction; external provider behavior remains unverified. Final gates after the corrected implementation: formatting 220 files clean, lint clean, mypy 201 source files clean, pytest 978 passed / 3 opt-in live-provider skips.

## Webhook retry-state follow-up

A repository review found that `claim_event` returned replay for every existing event, even though the domain state machine explicitly allows `FAILED` and `QUARANTINED` to transition back to `PROCESSING`. A failed redelivery therefore could never retry after a transient reconciliation or dependency failure. Both in-memory and SQLAlchemy repositories now atomically reclaim only FAILED/QUARANTINED records; PROCESSED records remain blocked. Regression tests cover retry attempt counting and processed replay rejection. Full gates after this state-machine change: formatting 221 files clean, lint clean, mypy 202 source files clean, pytest 980 passed / 3 opt-in live-provider skips. This does not make arbitrary quarantined events safe to replay without operational review; payload hash and provider identity still need deployment-level controls. A follow-up concurrency probe found two SQL callers could both observe FAILED and both reclaim it. SQL retry now uses a conditional `UPDATE ... WHERE status IN (FAILED, QUARANTINED)` semantics (single expected-state value), with exactly one winner; the SQLite concurrent regression passed. Final gates after the SQL race fix and typing correction: formatting 222 files clean, lint clean, mypy 203 source files clean, pytest 981 passed / 3 opt-in live-provider skips.

## 2026-11-18 follow-up

The shared-header construction warning above was reproduced with two barrier-synchronized requests: both responses initially received the second request's `x-request-id`. Oversized-request 413 paths also leaked trace context. Response-header mutation is now request-scoped, ASGI uses the returned response snapshot, and all early returns restore context. Focused request/transport/rate-limit regressions: 18 passed.

A supplied hosted OpenAI-compatible endpoint completed one real advisory request in 41.867s with explicit runtime transport settings. Later provider 503/502 responses exercised fail-closed fallback; no provider availability claim is made. Deterministic policy rejected a separate advisory `ALLOW` at confidence 1.0 when the action exceeded its hard limit. Control Center live pause/resume was confirmed by independent API state reads, and approval, keyboard, focus, scenario, responsive-layout, Node-test, and production-build checks passed. Browser evidence was DOM/accessibility/network based, not screenshot inspection.

Final local gates after these fixes: ruff format 219 files clean, ruff check clean, mypy 200 source files clean, pytest 972 passed / 3 opt-in skips; 7 web tests passed and the frontend production build passed. External payment, production deployment, external assessment, and certification remain unverified.
