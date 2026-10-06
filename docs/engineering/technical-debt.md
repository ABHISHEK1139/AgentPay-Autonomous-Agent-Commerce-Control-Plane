# Technical Debt Register

Every entry needs an id, a reason, an impact, and a planned resolution. An entry without a
resolution is a decision to accept the debt, and should say so explicitly.

## TD-001 — Blueprint §9 monorepo layout is not built

- **Reason:** creating empty directories would look like progress. Only the parts that hold real
  files exist.
- **Impact:** the repository does not yet look like its own specification. A new engineer may look
  for `apps/web` or `adapters/` and find nothing.
- **Resolution:** create each directory in the sprint that first needs it, with content. `apps/web`
  and `adapters/commerce` arrive with Sprint 3; `apps/worker` when background work exists.
- **Status:** resolved (Monorepo structure initialized per §9 with workspaces and packages)

## TD-002 — `Money` does not model the minor-unit exponent

- **Reason:** the MVP is single-currency, and embedding an ISO 4217 exponent table in the domain
  core would hard-code currency knowledge (§122) and create a second thing to keep current.
- **Impact:** a currency whose smallest unit is not 1/100 (JPY, KRW) cannot be represented
  correctly. Formatting a JPY amount would be wrong by a factor of 100.
- **Resolution:** resolve before any multi-currency work, by carrying an explicit exponent resolved
  at the adapter boundary from a maintained library, not from a table written into the domain.
- **Status:** open, tracked. **Blocking for:** multi-currency commerce.

## TD-003 — No `Makefile` or `justfile`

- **Reason:** `make` and `just` are not installed on the development machine, and a task runner
  that cannot run is worse than none.
- **Impact:** the commands are not discoverable from a single entry point; a new engineer must read
  this file or `README.md`.
- **Resolution:** add one when a runner is actually available, delegating to the same commands.
- **Status:** open, low priority

## TD-004 — No automated security scanning in CI

- **Reason:** the pipeline currently runs format, lint, type check, and tests. Scanning tools are
  not yet selected or configured.
- **Impact:** no automated evidence for secret scanning, dependency risk, or static analysis.
- **Resolution:** add secret scanning and dependency review in Sprint 9 (§75), and update
  `docs/security/standards-matrix.md` with the evidence they produce.
- **Status:** open, tracked

## TD-005 — Repository is not under version control

- **Reason:** `git init` has not been run.
- **Impact:** no history, no review trail, no CI run history, and blueprint §85 commit conventions
  and §84 worktrees cannot be followed. AI-generated-code provenance (§175) cannot be recorded.
- **Resolution:** initialise git, commit the Sprint 0 skeleton, and record provenance from here on.
- **Status:** resolved (Git repository initialized, provenance tracked)
