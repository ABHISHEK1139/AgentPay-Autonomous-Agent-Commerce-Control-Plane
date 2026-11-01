"""Dependency vulnerability audit runner (§57, TD-004).

Exports the locked runtime dependency set from `uv.lock`, audits it with a *pinned*
`pip-audit` executed ephemerally via `uv tool run` (so it never becomes a project
dependency), and fails closed if the export or the audit cannot run.

Usage: uv run python scripts/dependency_audit.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PIP_AUDIT_SPEC = "pip-audit==2.10.1"


def _info(message: str) -> None:
    """Print informational message to stdout without triggering T20 lint warnings."""
    sys.stdout.write(f"[*] {message}\n")
    sys.stdout.flush()


def _success(message: str) -> None:
    """Print success message to stdout without triggering T20 lint warnings."""
    sys.stdout.write(f"[+] SUCCESS: {message}\n")
    sys.stdout.flush()


def _fail(message: str) -> None:
    """Print failure message to stderr without triggering T20 lint warnings."""
    sys.stderr.write(f"[!] FAILED: {message}\n")
    sys.stderr.flush()


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        _fail("'uv' not found on PATH; cannot export locked dependencies.")
        return 1

    _info("Exporting locked runtime dependencies from uv.lock ...")
    export = _run(
        [uv, "export", "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements-txt"]
    )
    if export.returncode != 0:
        _fail(f"'uv export' exited {export.returncode}: {export.stderr.strip()}")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        requirements = Path(tmp) / "requirements-audit.txt"
        requirements.write_text(export.stdout, encoding="utf-8")

        _info(f"Auditing exported requirements with {PIP_AUDIT_SPEC} ...")
        audit = _run([uv, "tool", "run", PIP_AUDIT_SPEC, "-r", str(requirements)])
        output = (audit.stdout + audit.stderr).strip()
        if output:
            sys.stdout.write(f"{output}\n")
            sys.stdout.flush()

        if audit.returncode == 0:
            _success(f"dependency audit clean ({PIP_AUDIT_SPEC}).")
            return 0

        _fail(f"dependency audit reported problems (exit {audit.returncode}).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
