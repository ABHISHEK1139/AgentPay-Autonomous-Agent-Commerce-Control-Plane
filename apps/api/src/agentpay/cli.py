"""Command-line interface (CLI) for AgentPay (§10, §12).

Provides unified command dispatch for:
- `agentpay serve`: Run the HTTP API and optional Web UI.
- `agentpay worker`: Run the background outbox dispatcher and reconciliation worker.
- `agentpay health`: Check the health of a running AgentPay cluster.
- `agentpay migrate`: Run relational schema migrations.
- `agentpay version`: Display version information.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agentpay.api.factory import create_app
from agentpay.config.config import AppConfig
from agentpay.server import run_server
from agentpay.workers import BackgroundWorker


def _handle_serve(args: argparse.Namespace) -> int:
    host = args.host
    port = args.port
    db_url = args.db

    static_dir: Path | None = None
    if args.with_web:
        candidates = [
            Path("apps/web/dist").resolve(),
            Path(__file__).resolve().parents[4] / "apps" / "web" / "dist",
        ]
        for c in candidates:
            if c.exists() and (c / "index.html").exists():
                static_dir = c
                break
        if static_dir is None:
            print("[!] Warning: --with-web was specified but apps/web/dist was not found.")
            print("[!] Run 'npm --prefix apps/web run build' to generate the Web UI assets.")

    config = AppConfig.from_env()
    if db_url:
        config = config.with_overrides(database_url=db_url)
    app = create_app(config=config)
    run_server(host=host, port=port, app=app, static_dir=static_dir, database_url=db_url)
    return 0


def _handle_worker(args: argparse.Namespace) -> int:
    interval = args.interval
    print(f"[*] Starting AgentPay Background Worker (poll interval: {interval}s)...")

    # Wire worker with in-memory or database-backed repos
    worker = BackgroundWorker()
    running = True

    def _sig_handler(sig: int, frame: Any) -> None:
        nonlocal running
        print(f"\n[*] Received signal {sig}, stopping background worker gracefully...")
        running = False

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
    except (ValueError, AttributeError):
        pass  # Windows or non-main thread

    while running:
        try:
            stats = worker.run_once()
            if any(stats.values()):
                print(f"[*] Worker tick: {stats}")
        except Exception as exc:
            print(f"[!] Error in background worker tick: {exc}")
        time.sleep(interval)

    print("[+] Background worker stopped cleanly.")
    return 0


def _handle_health(args: argparse.Namespace) -> int:
    url = f"{args.url.rstrip('/')}/api/v1/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"[+] AgentPay is healthy (HTTP {resp.status})")
            print(json.dumps(data, indent=2))
            return 0
    except urllib.error.URLError as err:
        print(f"[-] Health check failed for {url}: {err}")
        return 1


def _handle_migrate(args: argparse.Namespace) -> int:
    try:
        from alembic import command
        from alembic.config import Config

        ini_candidates = [
            Path("alembic.ini").resolve(),
            Path("infrastructure/database/alembic.ini").resolve(),
            Path(__file__).resolve().parents[4] / "alembic.ini",
        ]
        ini_path = next((p for p in ini_candidates if p.exists()), None)
        if ini_path is None:
            print("[-] Could not locate alembic.ini configuration file.")
            return 1

        cfg = Config(str(ini_path))
        if args.db_url:
            cfg.set_main_option("sqlalchemy.url", args.db_url)

        print(f"[*] Running database migrations against {ini_path}...")
        command.upgrade(cfg, "head")
        print("[+] Migrations successfully applied to head.")
        return 0
    except Exception as exc:
        print(f"[-] Migration failed: {exc}")
        return 1


def _handle_version(_: argparse.Namespace) -> int:
    print("AgentPay Autonomous Agent Commerce Control Plane v0.1.0")
    print(f"Python Runtime: {sys.version.split()[0]} ({sys.platform})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentpay",
        description="AgentPay: Autonomous Agent Commerce Control Plane CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start the AgentPay HTTP server")
    serve_parser.add_argument(
        "--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)"
    )
    serve_parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    serve_parser.add_argument(
        "--with-web", action="store_true", help="Serve pre-built web dashboard SPA"
    )
    serve_parser.add_argument("--db", default=None, help="Optional database URL")

    # worker
    worker_parser = subparsers.add_parser("worker", help="Start the background worker daemon")
    worker_parser.add_argument(
        "--interval", type=float, default=1.0, help="Polling interval in seconds"
    )

    # health
    health_parser = subparsers.add_parser("health", help="Check cluster health")
    health_parser.add_argument(
        "--url", default="http://127.0.0.1:8000", help="Base URL of AgentPay cluster"
    )

    # migrate
    migrate_parser = subparsers.add_parser("migrate", help="Run database migrations")
    migrate_parser.add_argument("--db-url", default=None, help="Database connection URL override")

    # version
    subparsers.add_parser("version", help="Print version information")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "serve":
        return _handle_serve(args)
    if args.command == "worker":
        return _handle_worker(args)
    if args.command == "health":
        return _handle_health(args)
    if args.command == "migrate":
        return _handle_migrate(args)
    if args.command == "version":
        return _handle_version(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
