# Known Limitations

Things a future engineer will otherwise discover the hard way.

## Operational & Environment Limitations

- **Hermetic In-Memory Execution**: The local development machine does not have a running Docker daemon
  (`docker info` fails). All 381 automated backend tests and 3 web dashboard tests run against thread-safe
  in-memory repository adapters and sandbox payment providers. Production PostgreSQL and Redis connection
  pools are defined in configuration but run hermetically in local test runs.
- **Task Runner Tools**: `make` and `just` are not installed on this machine (TD-003). All operations
  are executed directly via `uv run` and `node --test`.

## Domain & Commerce Limitations

- **Currency Minor-Unit Exponents (TD-002)**: The `Money` value object uses integer minor units (paise/cents)
  with an ISO currency string. It does not carry an explicit exponent table, so zero-decimal currencies
  (e.g., JPY, KRW) or 3-decimal currencies (e.g., BHD, KWD) are not currently handled. This is tracked in TD-002
  and blocks multi-currency deployment.
- **Single-Item Atomic Purchase Slice**: While the data model supports carts and multi-item orders, the primary
  hardened orchestrator (`PurchaseOrchestrator`) specializes in atomic single-variant purchase actions with
  pre-flight conflict detection.
- **Provider Sandbox Mode**: The payment subsystem uses a verified Mock / Sandbox payment provider implementing
  the complete payment state machine (`CREATED`, `AUTHORIZED`, `CAPTURED`, `FAILED`, `UNKNOWN`, `REFUNDED`),
  idempotency checking, and saga compensation. Direct production gateway keys (live Razorpay production)
  are not active in local test environments.

## AI & Intelligence Boundaries

- **Advisory AI Simulation**: The decision engine (JEV / LLM) operates via mock and structured decision adapters
  to ensure deterministic, reproducible test execution. While external LLM gateways can be plugged in via
  the application decision interface, live model API keys are omitted from hermetic test suites.
- **Deterministic Policy Priority (§2.2, §134)**: Even with advanced model integrations, the policy engine
  strictly refuses any model recommendation that exceeds configured financial thresholds.

## Security & Scanning

- **CI Scanning Tools (TD-004)**: Static type checking (`mypy`), linting (`ruff`), and internal adversarial
  harnesses run on every commit. Third-party commercial SAST, container vulnerability scanners, and external
  penetration testing have not been conducted.
