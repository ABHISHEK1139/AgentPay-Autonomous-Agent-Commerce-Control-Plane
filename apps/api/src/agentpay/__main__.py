"""Main entrypoint for running AgentPay via python -m agentpay (§10, §12)."""

from __future__ import annotations

import sys

from agentpay.cli import main

if __name__ == "__main__":
    sys.exit(main())
