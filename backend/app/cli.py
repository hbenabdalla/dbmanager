"""Operational CLI.

python -m app.cli migrate            # alembic upgrade head
python -m app.cli bootstrap          # roles, environments, first admin
python -m app.cli simulation status                         # simulated (demo) fleet status
python -m app.cli simulation generate --profile small [--seed 42]
python -m app.cli simulation cleanup --dry-run              # show what would be removed
python -m app.cli simulation cleanup --yes                  # remove all simulated data
python -m app.cli metrics status                            # monitoring storage (time-series DB) status
python -m app.cli metrics migrate-to-timeseries --dry-run    # copy legacy PostgreSQL metric history
    [--batch-size N] [--from-date YYYY-MM-DD] [--to-date YYYY-MM-DD] [--server-id UUID] [--restart]
python -m app.cli gen-master-key     # print a new base64 master key
python -m app.cli rotate-master-key  # re-wrap secrets with the current DBM_MASTER_KEY
python -m app.cli reset-password <username>   # set a new password (prompted)
python -m app.cli matrix             # print the capability support matrix (markdown)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging


def run_migrations() -> None:
    from alembic.config import Config

    from alembic import command

    base = Path(__file__).resolve().parent.parent
    cfg = Config(str(base / "alembic.ini"))
    cfg.set_main_option("script_location", str(base / "alembic"))
    command.upgrade(cfg, "head")


async def _bootstrap() -> None:
    from app.db.session import dispose_engine, session_scope
    from app.seed.bootstrap import bootstrap

    async with session_scope() as db:
        await bootstrap(db)
    await dispose_engine()


def _fmt(n: object) -> str:
    return f"{n:,}" if isinstance(n, int) and not isinstance(n, bool) else str(n)


def _print_report(report: dict) -> None:  # type: ignore[type-arg]
    c = report["counts"]
    rows = [
        ("Simulated servers", c["servers"]),
        ("Databases", c["databases"]),
        ("Metrics", c["metrics"]),
        ("Query statistics", c["queries"]),
        ("Security findings", c["security_findings"]),
        ("Recommendations", c["recommendations"]),
        ("Sites", len(report["groupings"]["sites"]["remove"])),
        ("Clusters", len(report["groupings"]["clusters"]["remove"])),
        ("Applications", len(report["groupings"]["applications"]["remove"])),
    ]
    width = max(len(r[0]) for r in rows)
    for label, n in rows:
        print(f"{label + ':':<{width + 2}}{_fmt(n):>12}")
    for kind, g in report["groupings"].items():
        if g["remove"]:
            print(f"  {kind} to remove: {', '.join(g['remove'])}")
        if g["keep"]:
            print(f"  {kind} kept (a real server uses them): {', '.join(g['keep'])}")
    print(f"Real servers (not affected): {_fmt(c['real_servers'])}")
    if report["legacy_demo_accounts"]:
        print(
            "\nAccounts created by the old DEMO MODE are NOT removed automatically: "
            + ", ".join(report["legacy_demo_accounts"])
            + ".\nReview them under Platform -> Users and disable or delete the ones you do not need."
        )


async def _simulation(args: argparse.Namespace) -> None:
    from app.db.session import dispose_engine, session_scope
    from app.simulation import service

    try:
        if args.action == "status":
            async with session_scope() as db:
                st = await service.status(db)
            for k in (
                "simulation_present",
                "profile",
                "seed",
                "generated_at",
                "generated_by",
                "servers",
                "real_servers",
                "databases",
                "telemetry_samples",
                "query_fingerprints",
                "security_findings",
                "recommendations",
            ):
                print(f"{k}: {_fmt(st.get(k))}")
            if st["active_run"]:
                r = st["active_run"]
                print(f"active run: {r['operation']} {r['status']} {r['progress']}% - {r['phase']}")
        elif args.action == "generate":
            async with session_scope() as db:
                op = "regenerate" if args.replace else "generate"
                run = await service.request_generation(db, None, profile=args.profile, seed=args.seed, operation=op)
                print(f"queued {op} run {run.id} (profile {run.profile}, seed {run.seed}); generating now...")
            result = await service.execute_pending()
            print({k: v for k, v in result.items() if k != "plan"})
        elif args.action == "cleanup":
            async with session_scope() as db:
                report = await service.cleanup_report(db)
            _print_report(report)
            if args.dry_run:
                print("\nNothing deleted - dry run.")
                return
            if not report["counts"]["servers"] and not any(g["remove"] for g in report["groupings"].values()):
                print("\nNo simulated data to remove.")
                return
            if not args.yes:
                if not sys.stdin.isatty():
                    sys.exit("\nRefusing to delete without confirmation: re-run with --yes (or --dry-run).")
                answer = await asyncio.to_thread(input, "\nType REMOVE to delete all simulated data: ")
                if answer.strip() != "REMOVE":
                    sys.exit("Aborted - nothing deleted.")
            async with session_scope() as db:
                result = await service.remove(db, None)
            print("\nRemoved:", {k: v for k, v in result.items() if v})
    finally:
        from app.timeseries.repository import get_repository
        from app.timeseries.writer import get_writer

        await get_writer().flush_all()
        await get_repository().close()
        await dispose_engine()


def _date(value: str) -> datetime:
    d = datetime.fromisoformat(value)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


async def _metrics(args: argparse.Namespace) -> None:
    from app.db.session import dispose_engine, session_scope
    from app.timeseries.repository import TimeSeriesUnavailable, get_repository

    try:
        if args.action == "status":
            from app.services import monitoring_storage

            async with session_scope() as db:
                st = await monitoring_storage.status(db)
            for k in ("status", "engine", "version", "series", "samples", "oldest_metric", "newest_metric", "error"):
                print(f"{k}: {_fmt(st.get(k))}")
            r = st["retention"]
            print(f"retention: desired {r['desired_days']}d, effective {r['effective']} (in sync: {r['in_sync']})")
            print(f"pending deletes: {st['pending_deletes']}")
        elif args.action == "migrate-to-timeseries":
            import uuid as _uuid

            from app.timeseries.migration import MigrationOptions, migrate

            opts = MigrationOptions(
                dry_run=args.dry_run,
                batch_size=max(100, min(args.batch_size, 500_000)),
                from_date=_date(args.from_date) if args.from_date else None,
                to_date=_date(args.to_date) if args.to_date else None,
                server_id=_uuid.UUID(args.server_id) if args.server_id else None,
                restart=args.restart,
            )
            try:
                rep = await migrate(session_scope, opts)
            except TimeSeriesUnavailable as exc:
                sys.exit(f"time-series database unavailable: {exc}. Progress is checkpointed; re-run to resume.")
            print({k: v for k, v in rep.as_dict().items()})
    finally:
        await get_repository().close()
        await dispose_engine()


async def _rotate() -> None:
    from app.db.session import dispose_engine, session_scope
    from app.services.secrets import rewrap_all

    async with session_scope() as db:
        n = await rewrap_all(db)
    print(f"re-wrapped {n} secrets with key id {get_settings().master_key_id}")
    await dispose_engine()


async def _reset_password(username: str) -> None:
    from sqlalchemy import select

    from app.core.security import hash_password, password_policy_errors
    from app.db.session import dispose_engine, session_scope
    from app.models import User

    pw = getpass.getpass("New password: ")
    errs = password_policy_errors(pw)
    if errs:
        sys.exit("; ".join(errs))
    async with session_scope() as db:
        u = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if u is None:
            sys.exit("no such user")
        u.password_hash, u.failed_logins, u.locked_until, u.auth_source = hash_password(pw), 0, None, "local"
    print("password updated")
    await dispose_engine()


def print_matrix() -> None:
    """Implemented capabilities (adapter ``static_capabilities``) as a markdown table."""
    from app.adapters.base import Capability
    from app.adapters.registry import supported_engines

    short = {
        "FULL": "FULL",
        "PARTIAL": "PARTIAL",
        "NOT_SUPPORTED": "—",
        "REQUIRES_PRIVILEGE": "PRIV",
        "REQUIRES_LICENSE": "LICENSE",
        "REQUIRES_CONFIGURATION": "CONFIG",
        "REQUIRES_EXTENSION": "EXT",
        "REQUIRES_EXTERNAL_MONITORING": "EXTERNAL",
        "NOT_IMPLEMENTED": "NOT IMPL",
        "PLANNED": "NOT IMPL",
    }
    engines = supported_engines()
    names = list(engines)
    print("| Capability | " + " | ".join(engines[n].display_name for n in names) + " |")
    print("|---|" + "---|" * len(names))
    for c in Capability:
        cells = [short.get(engines[n].capability_profile()[c].level.value, "?") for n in names]
        print(f"| {c.value.replace('_', ' ')} | " + " | ".join(cells) + " |")


def main() -> None:
    configure_logging(get_settings().log_level, as_json=False)
    p = argparse.ArgumentParser(prog="dbm")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sub.add_parser("bootstrap")
    sim = sub.add_parser("simulation", help="manage the simulated (demo) fleet")
    sim_sub = sim.add_subparsers(dest="action", required=True)
    sim_sub.add_parser("status")
    g = sim_sub.add_parser("generate")
    g.add_argument("--profile", default="small")
    g.add_argument("--seed", type=int, default=None)
    g.add_argument("--replace", action="store_true", help="remove existing simulated data first (regenerate)")
    c = sim_sub.add_parser("cleanup")
    c.add_argument("--dry-run", action="store_true", help="only show what would be removed")
    c.add_argument("--yes", action="store_true", help="remove without the interactive confirmation")
    met = sub.add_parser("metrics", help="monitoring telemetry storage (time-series database)")
    met_sub = met.add_subparsers(dest="action", required=True)
    met_sub.add_parser("status")
    mg = met_sub.add_parser("migrate-to-timeseries", help="copy legacy PostgreSQL metric history")
    mg.add_argument("--dry-run", action="store_true", help="count what would be migrated, write nothing")
    mg.add_argument("--batch-size", type=int, default=20_000, help="legacy rows per batch (default 20000)")
    mg.add_argument("--from-date", help="only rows at or after this UTC date/time (ISO 8601)")
    mg.add_argument("--to-date", help="only rows before this UTC date/time (ISO 8601)")
    mg.add_argument("--server-id", help="only this server (UUID)")
    mg.add_argument("--restart", action="store_true", help="ignore the saved checkpoint and start over")
    sub.add_parser("gen-master-key")
    sub.add_parser("rotate-master-key")
    r = sub.add_parser("reset-password")
    r.add_argument("username")
    sub.add_parser("matrix")
    a = p.parse_args()
    if a.cmd == "migrate":
        run_migrations()
    elif a.cmd == "bootstrap":
        asyncio.run(_bootstrap())
    elif a.cmd == "simulation":
        asyncio.run(_simulation(a))
    elif a.cmd == "metrics":
        asyncio.run(_metrics(a))
    elif a.cmd == "gen-master-key":
        from app.core.crypto import generate_master_key

        print(generate_master_key())
    elif a.cmd == "rotate-master-key":
        asyncio.run(_rotate())
    elif a.cmd == "reset-password":
        asyncio.run(_reset_password(a.username))
    elif a.cmd == "matrix":
        print_matrix()


if __name__ == "__main__":
    main()
