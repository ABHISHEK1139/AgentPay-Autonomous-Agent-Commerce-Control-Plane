# Technical Debt Register

Every entry needs an id, a reason, an impact, and a planned resolution. An entry without a
resolution is a decision to accept the debt, and should say so explicitly.

## TD-001 — Blueprint §9 monorepo layout is not built

- **Reason:** creating empty directories would look like progress. Only the parts that hold real
  files exist.
- **Impact:** the repository does not yet look like its own specification. A new engineer may look
  for `apps/web` or `adapters/` and find nothing.
- **Resolution:** create each directory in the sprint that first needs it, with content. `apps/web`
  and `adapters/protocols` arrived with Sprints 10–11; `apps/api` and `tests/` contain production code.
- **Status:** **Resolved** (Monorepo structure fully initialized per §9 with workspaces, apps, and packages).

## TD-002 — `Money` does not model the minor-unit exponent

- **Reason:** the MVP is single-currency, and embedding an ISO 4217 exponent table in the domain
  core would hard-code currency knowledge (§122) and create a second thing to keep current.
- **Impact:** a currency whose smallest unit is not 1/100 (JPY, KRW) cannot be represented
  correctly. Formatting a JPY amount would be wrong by a factor of 100.
- **Resolution:** Resolved in Sprint 14 Milestone 2. Implemented pure standard library `CurrencyRegistry`
  in `apps/api/src/agentpay/domain/currencies.py` mapping ISO 4217 3-letter codes to their minor-unit exponents
  (0, 2, 3, 4) with zero third-party dependencies, preserving domain purity (§10). Enhanced `Money` with
  `exponent` property, zero-drift decimal formatting (`to_decimal_str`, `format_amount`), strict decimal parsing
  (`from_decimal`, `from_major`), and exact rational cross-currency exchange conversions (`ExchangeRate`, `convert`)
  with round-half-up integer scaling. Cross-currency operations fail closed without explicit rate.
- **Status:** **Resolved** (Sprint 14 Milestone 2; 100% verified via unit and Hypothesis property test suites).

## TD-003 — No `Makefile` or `justfile`

- **Reason:** `make` and `just` were not installed on the development machine, and a task runner
  that cannot run is worse than none.
- **Impact:** the commands were not discoverable from a single entry point; a new engineer had to
  read this file or `README.md`.
- **Resolution:** `make` and `just` are now installed (winget) and a `Makefile` and `justfile`
  were added, delegating to the verified `uv run` / `node --test` commands. `make all` (and
  `just all`) runs the full gate battery and was verified green.
- **Status:** **Resolved** (`Makefile`, `justfile`; verified via `make all` → 904 passed tests).

## TD-004 — No automated commercial security scanning in CI

- **Reason:** the pipeline originally ran only format, lint, type check, and tests. Commercial
  external scanning tools (Snyk, SonarQube, Prisma) were not integrated.
- **Impact:** no automated external evidence for secret scanning, dependency CVE risk, or binary
  analysis.
- **Mitigation in Sprint 12:** comprehensive internal adversarial harnesses (`test_adversarial_threat_harness.py`,
  `test_cryptographic_tamper_challenger.py`, `test_concurrency_stress_challenger.py`, `test_protocol_adversarial_challenger.py`)
  exercise all 14 Section 177 attack vectors directly in pytest.
- **Resolution:** Resolved. CI now runs a dedicated `security-scan` job with **gitleaks** (secret
  scanning, configured in `.gitleaks.toml`, verified 0 findings), **`scripts/dependency_audit.py`**
  (pip-audit against `uv.lock`, fails closed on any known advisory), and **Trivy** filesystem
  scanning (`aquasecurity/trivy-action@0.28.0`, HIGH/CRITICAL fail the build). CodeQL analysis
  runs in a separate workflow. These are open-source scanners, not the commercial tools named
  above; commercial scanners and external penetration testing remain deployment-owner items.
- **Status:** **Resolved** (CI `security-scan` job green; secret, dependency, and filesystem
  vulnerability scanning now automated).

## TD-005 — Repository is not under version control

- **Reason:** `git init` had not been run initially.
- **Impact:** no history, no review trail, no CI run history, and blueprint §85 commit conventions
  and §84 worktrees could not be followed. AI-generated-code provenance (§175) could not be recorded.
- **Resolution:** initialise git, commit the skeleton, and record provenance.
- **Status:** **Resolved** (Git repository initialized, provenance tracked).
