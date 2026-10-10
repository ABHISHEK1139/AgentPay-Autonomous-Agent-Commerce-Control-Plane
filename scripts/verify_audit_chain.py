#!/usr/bin/env python3
"""scripts/verify_audit_chain.py - Independent Audit Ledger Verification Tool (§45, §46, §177).

Validates the cryptographic integrity of an exported JSONL audit ledger stream.
Recomputes SHA-256 preimages over canonical JSON and verifies sequential hash chaining.
Exit codes:
  0 - All audit records across all tenants are cryptographically valid.
  1 - Verification failed (tampered data, broken linkage, or malformed file).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Ensure pure domain modules can be resolved regardless of execution directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api" / "src"))

from agentpay.domain.audit import GENESIS_HASH, AuditHasher


def log_info(msg: str) -> None:
    """Print informational message to stdout without triggering T20 lint warnings."""
    sys.stdout.write(f"[*] {msg}\n")
    sys.stdout.flush()


def log_success(msg: str) -> None:
    """Print success message to stdout without triggering T20 lint warnings."""
    sys.stdout.write(f"[+] SUCCESS: {msg}\n")
    sys.stdout.flush()


def log_error(msg: str) -> None:
    """Print error message to stderr without triggering T20 lint warnings."""
    sys.stderr.write(f"[!] FAILED: {msg}\n")
    sys.stderr.flush()


def verify_exported_stream(file_path: str) -> bool:
    """Verify cryptographic hash chaining for all events in a JSONL dump file."""
    path = Path(file_path)
    if not path.is_file():
        log_error(f"Audit dump file not found: {file_path}")
        return False

    log_info(f"Loading audit ledger stream from: {path}")

    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_no, raw_line in enumerate(f, 1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        log_error(f"Line {line_no} is not a valid JSON object")
                        return False
                    records.append(record)
                except json.JSONDecodeError as err:
                    log_error(f"Line {line_no} contains invalid JSON: {err}")
                    return False
    except OSError as err:
        log_error(f"Unable to read file {file_path}: {err}")
        return False

    if not records:
        log_info(f"Audit dump file is empty: {file_path}. Zero events to verify.")
        return True

    # Partition events by tenant_id
    tenant_events: dict[str, list[dict[str, Any]]] = {}
    for idx, r in enumerate(records):
        tenant_id = r.get("tenant_id")
        if not tenant_id or not isinstance(tenant_id, str):
            log_error(f"Record at index {idx} missing valid 'tenant_id'")
            return False
        tenant_events.setdefault(tenant_id, []).append(r)

    total_verified = 0

    for tenant_id, events in tenant_events.items():
        log_info(f"Verifying tenant: {tenant_id} ({len(events)} events)...")
        for idx, ev in enumerate(events):
            event_id = str(ev.get("event_id", f"index_{idx}"))

            # Validate required fields
            prev_hash = ev.get("previous_event_hash")
            curr_hash = ev.get("event_hash")
            if not prev_hash or not isinstance(prev_hash, str):
                log_error(f"Event {event_id} missing valid 'previous_event_hash'")
                return False
            if not curr_hash or not isinstance(curr_hash, str):
                log_error(f"Event {event_id} missing valid 'event_hash'")
                return False

            # Check previous hash link
            expected_prev = GENESIS_HASH if idx == 0 else events[idx - 1]["event_hash"]
            if prev_hash != expected_prev:
                log_error(
                    f"Broken linkage at event {event_id}: previous_event_hash "
                    f"'{prev_hash}' does not match expected '{expected_prev}'"
                )
                return False

            # Recompute event hash
            event_data = ev.get("data")
            if not isinstance(event_data, dict):
                log_error(f"Event {event_id} 'data' must be a JSON object, got {type(event_data)}")
                return False

            actor = str(ev.get("actor", ""))
            event_type = str(ev.get("event_type", ""))
            timestamp = str(ev.get("timestamp", ""))
            agent_id = ev.get("agent_id")
            action_id = ev.get("action_id")

            recomputed = AuditHasher.compute_event_hash(
                event_data=event_data,
                previous_hash=prev_hash,
                actor=actor,
                event_type=event_type,
                timestamp=timestamp,
                tenant_id=tenant_id,
                agent_id=str(agent_id) if agent_id is not None else None,
                action_id=str(action_id) if action_id is not None else None,
            )

            if curr_hash != recomputed:
                log_error(
                    f"Content tamper detected at event {event_id}: event_hash "
                    f"'{curr_hash}' does not match recomputed hash '{recomputed}'"
                )
                return False

            total_verified += 1

    log_success(f"All {total_verified} events verified across {len(tenant_events)} tenants.")
    return True


def main() -> int:
    """CLI entrypoint."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.stdout.write(
            "Usage: uv run python scripts/verify_audit_chain.py <path_to_audit_dump.jsonl>\n"
        )
        return 0 if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help") else 1

    file_path = sys.argv[1]
    success = verify_exported_stream(file_path)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
