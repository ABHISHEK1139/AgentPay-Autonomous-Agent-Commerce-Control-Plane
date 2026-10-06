# Standards Matrix

Status: Sprint 0 mapping only. Read the honesty rule below before reusing this table.

## Honesty rule

Four distinct claims, never interchangeable:

- **designed for** — the architecture accounts for the requirement
- **aligned with** — the process follows the guidance
- **tested against** — a specific automated or manual test exercises it
- **certified by** — an external body formally attested it

AgentPay is **not certified** by any body listed here. No assessment, penetration test, or external
audit has taken place. Standards versions change; confirm the current published version of a
standard before mapping a requirement to a specific clause number.

## Current mapping

| Standard | Relationship | Evidence today | Gap |
| --- | --- | --- | --- |
| OWASP ASVS | designed for | architectural boundaries and money handling in `docs/architecture/architecture.md` | full requirement-level mapping not started (`security/asvs/mapping.md`) |
| OWASP API Security Top 10 | designed for | blueprint §52 | no API exists yet; nothing tested against |
| OWASP Agentic AI guidance | designed for | AI below the authorization boundary (§2, §18) | no agent or model integration exists yet |
| NIST AI RMF | aligned with | governance framing in blueprint §§144–146 | no AI system to measure or manage yet |
| NIST SSDF | aligned with | CI gates, locked dependencies, ADR and threat-model practice | pipeline not yet hardened beyond lint/type/test |
| OAuth 2.0 Security BCP | designed for | blueprint §14 | no authentication implemented |
| SLSA | aligned with | CI builds from a pinned lockfile | no provenance attestation or signing |
| PCI DSS | designed for | payment-data minimisation, no card data stored (§40) | no payment integration in scope yet |
| India DPDP | designed for | data classification and minimisation (§68, §69) | no lawful-basis mapping; phased commencement not analysed |

## Explicitly not true yet

- No standard has been assessed by an external party.
- No vulnerability scanning, SAST, dependency scanning, container scanning, or secret scanning has
  been run against this code.
- No access control has been tested because no access control is implemented.
- The threat model in `docs/threat-model/` is a self-assessment.

## Next steps

1. `security/asvs/mapping.md` with per-requirement status (REQUIRED / IMPLEMENTED / TESTED /
   VERIFIED / EXCEPTION).
2. Add secret scanning and dependency review to CI, then update this table with the evidence.
3. Re-check every version claim in blueprint §144 against the current published standard before
   citing a clause number.
