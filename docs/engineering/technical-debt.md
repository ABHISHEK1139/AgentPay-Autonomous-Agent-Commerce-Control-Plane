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
- **Resolution:** resolve before any multi-currency work, by carrying an explicit exponent resolved
  at the adapter boundary from a maintained library, not from a table written into the domain.
- **Status:** **Open, tracked.** **Blocking for:** multi-currency commerce.

## TD-003 — No `Makefile` or `justfile`

- **Reason:** `make` and `just` are not installed on the development machine, and a task runner
  that cannot run is worse than none.
- **Impact:** the commands are not discoverable from a single entry point; a new engineer must read
  this file or `README.md`.
- **Resolution:** add one when a runner is actually available in the build environment, delegating to `uv run` and `node --test`.
- **Status:** **Open, low priority.**

## TD-004 — No automated commercial security scanning in CI

- **Reason:** the pipeline currently runs format, lint, type check, and tests. Commercial external scanning tools
  (Snyk, SonarQube, Prisma) are not yet integrated into the repository.
- **Impact:** no automated external evidence for secret scanning, dependency CVE risk, or static binary analysis.
- **Mitigation in Sprint 12:** comprehensive internal adversarial harnesses (`test_adversarial_threat_harness.py`,
  `test_cryptographic_tamper_challenger.py`, `test_concurrency_stress_challenger.py`, `test_protocol_adversarial_challenger.py`)
  exercise all 14 Section 177 attack vectors directly in pytest.
- **Resolution:** integrate GitHub Secret Scanning, Dependabot, and SAST action runners into CI before production cutover.
- **Status:** **Open, tracked.**

## TD-005 — Repository is not under version control

- **Reason:** `git init` had not been run initially.
- **Impact:** no history, no review trail, no CI run history, and blueprint §85 commit conventions
  and §84 worktrees could not be followed. AI-generated-code provenance (§175) could not be recorded.
- **Resolution:** initialise git, commit the skeleton, and record provenance.
- **Status:** **Resolved** (Git repository initialized, provenance tracked).
