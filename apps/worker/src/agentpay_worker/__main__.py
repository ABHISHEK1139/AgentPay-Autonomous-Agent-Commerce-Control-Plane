"""Main entrypoint for running AgentPay Background Worker via python -m agentpay_worker (§104, §106)."""

from __future__ import annotations

import argparse
import sys

from agentpay.cli import _handle_worker


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentPay Standalone Background Worker Runtime")
    parser.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds")
    args = parser.parse_args()
    return _handle_worker(args)


if __name__ == "__main__":
    sys.exit(main())
