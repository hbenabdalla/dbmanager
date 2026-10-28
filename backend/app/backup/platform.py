"""Platform backup: the control-plane database as a portable, versioned archive.

Archive layout (format ``dbmanager-platform/1``, a tar file, optionally age-encrypted)::

    manifest.json                 what is inside: versions, components, row counts, checksums
    tables/<table>.jsonl          one JSON object per row, columns by name
    metrics/victoriametrics.native   (optional) telemetry, VictoriaMetrics native export format

Each table belongs to exactly one *component* or is explicitly *excluded* (operational state that
is rebuilt on its own: sessions, rate-limit windows, job queues, telemetry, alert history, the
backup history itself). ``tests/unit/test_backup_platform.py`` fails when a table is added
without being classified.

Secrets stay exactly as stored: credentials, provider secrets, TOTP seeds … remain
envelope-encrypted with the platform master key, password hashes stay hashes. The manifest
lists the master-key ids those values need (``key_ids``); the key itself is never included.

Restore replaces the selected components: rows absent from the backup are deleted, rows in the
backup are inserted or overwritten (by primary key). The *audit* component is merge-only: missing
events are added back, nothing is ever deleted or overwritten. A restore always runs once as a
trial inside a transaction that is rolled back, to report its exact impact (including rows that
foreign keys would cascade-delete in other tables) before anything changes.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import hashlib
import json
import tarfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.db.base import Base

FORMAT = "dbmanager-platform/1"

#: component → tables (FK order inside a component does not matter: restore sorts globally)
COMPONENTS: dict[str, tuple[str, ...]] = {
    "organization": ("organizations", "environments", "sites", "clusters", "applications"),
    "inventory": ("servers", "server_tags", "credentials", "database_entries", "db_accounts"),
    "identity": (
        "users",
        "groups",
        "group_members",
        "roles",
        "role_assignments",
        "identity_providers",
        "group_mappings",
        "identities",
        "api_tokens",
    ),
    "findings": ("recommendations", "security_findings", "query_plan_snapshots"),
    "alerting": ("alert_rules", "maintenance_windows", "notification_channels", "report_schedules"),
    "settings": ("app_settings", "siem_destinations"),
    "backup_config": ("backup_targets", "backup_policies"),
    "audit": ("audit_events", "query_history"),
}
COMPONENT_LABELS = {
    "organization": "Organizations, environments, sites, clusters and applications",
    "inventory": "Registered servers, tags, encrypted credential references, discovered databases and accounts",
    "identity": "Users, groups, roles, role assignments, identity providers, group mappings, API tokens",
    "findings": "Recommendations and security findings with their triage state",
    "alerting": "Alert rules, maintenance windows, notification channels and report schedules",
    "settings": "Platform settings and audit forwarding destinations",
    "backup_config": "Backup storage targets and policies",
    "audit": "Audit log and SQL workspace history (merged on restore, never deleted)",
}
#: merge-only components: restore adds missing rows, never deletes or overwrites
MERGE_ONLY = frozenset({"audit"})
DEFAULT_COMPONENTS = tuple(c for c in COMPONENTS if c != "audit")

#: tables deliberately not in a platform backup, with the reason
EXCLUDED: dict[str, str] = {
    "user_sessions": "sign-in sessions are short-lived; users sign in again",
    "rate_limit_windows": "rate-limit counters are transient",
    "collector_jobs": "the job queue is rebuilt by the scheduler",
    "metric_samples": "legacy telemetry (time-series data is backed up separately)",
    "query_stat_snapshots": "legacy telemetry (time-series data is backed up separately)",
    "query_fingerprints": "query telemetry is recollected from the databases",
    "simulation_runs": "simulation run history; simulated servers themselves are inventory",
    "alerts": "alert history: alerts are re-evaluated from rules",
    "alert_comments": "alert history",
    "notification_deliveries": "notification delivery log",
    "backup_jobs": "backup job history stays with the running platform",
    "backups": "the backup catalog stays with the running platform",
    "report_runs": "report delivery history",
    "platform_instances": "running processes report themselves again after a restore",
}

TABLE_COMPONENT = {t: c for c, ts in COMPONENTS.items() for t in ts}


class PlatformBackupError(Exception):
    pass


def tables() -> dict[str, sa.Table]:
    return dict(Base.metadata.tables)


def unclassified() -> list[str]:
    return sorted(set(tables()) - set(TABLE_COMPONENT) - set(EXCLUDED))


def check_components(components: list[str] | tuple[str, ...]) -> list[str]:
    unknown = sorted(set(components) - set(COMPONENTS))
    if unknown:
        raise PlatformBackupError(f"unknown components: {', '.join(unknown)}")
    if not components:
        raise PlatformBackupError("select at least one component")
    return [c for c in COMPONENTS if c in components]


def ordered(names: set[str]) -> list[sa.Table]:
    """Tables in foreign-key dependency order (parents first)."""
    return [t for t in Base.metadata.sorted_tables if t.name in names]


# ---- value encoding ---------------------------------------------------------------------------
def _enc(v: Any) -> Any:
    if isinstance(v, dt.datetime | dt.date | dt.time):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, bytes | memoryview):
        return {"$b64": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    raise TypeError(f"cannot encode {type(v).__name__}")


def _decoder(col: sa.Column[Any]) -> Any:
    t = col.type
    if isinstance(t, sa.DateTime):
        return dt.datetime.fromisoformat
    if isinstance(t, sa.Date):
        return dt.date.fromisoformat
    if isinstance(t, sa.Time):
        return dt.time.fromisoformat
    if isinstance(t, sa.Uuid):
        return uuid.UUID
    if isinstance(t, sa.Numeric) and not isinstance(t, sa.Float):
        return decimal.Decimal
    if isinstance(t, sa.LargeBinary):
        return lambda v: base64.b64decode(v["$b64"])
    if isinstance(t, sa.Interval):
        return lambda v: dt.timedelta(seconds=v)
    return None


def decode_row(table: sa.Table, row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in row.items():
        col = table.c.get(name)
        if col is None:
            continue  # a column dropped since the backup was taken (reported by compatibility)
        dec = _decoder(col)
        out[name] = dec(value) if (dec and value is not None) else value
    return out


def _key_ids(value: Any, found: set[str]) -> None:
    """Master-key ids referenced by envelope-encrypted values (``{"key_id", "wrapped_dek", …}``)."""
    if isinstance(value, dict):
        if "key_id" in value and "wrapped_dek" in value:
            found.add(str(value["key_id"]))
        for v in value.values():
            _key_ids(v, found)
    elif isinstance(value, list):
        for v in value:
            _key_ids(v, found)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- export -----------------------------------------------------------------------------------
async def export_tables(db: AsyncSession, components: list[str], workdir: Path) -> dict[str, Any]:
    """Write ``tables/<name>.jsonl`` for the selected components inside one REPEATABLE READ
    snapshot (consistent across tables). Returns per-table counts/checksums and key ids."""
    await db.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    (workdir / "tables").mkdir(parents=True, exist_ok=True)
    info: dict[str, Any] = {"tables": {}, "key_ids": set()}
    names = {t for c in components for t in COMPONENTS[c]}
    for table in ordered(names):
        path = workdir / "tables" / f"{table.name}.jsonl"
        n = 0
        with open(path, "w", encoding="utf-8") as f:
            result = await db.stream(select(table).execution_options(yield_per=1000))
            async for row in result.mappings():
                d = dict(row)
                _key_ids(d, info["key_ids"])
                f.write(json.dumps(d, default=_enc, ensure_ascii=False, separators=(",", ":")))
                f.write("\n")
                n += 1
        info["tables"][table.name] = {
            "component": TABLE_COMPONENT[table.name],
            "rows": n,
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
            "columns": [c.name for c in table.columns],
        }
    info["key_ids"] = sorted(info["key_ids"])
    return info


def build_archive(workdir: Path, manifest: dict[str, Any], out: Path) -> None:
    (workdir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    with tarfile.open(out, "w") as tar:
        tar.add(workdir / "manifest.json", arcname="manifest.json")
        for sub in ("tables", "metrics"):
            d = workdir / sub
            if d.is_dir():
                for p in sorted(d.iterdir()):
                    tar.add(p, arcname=f"{sub}/{p.name}")


# ---- reading an archive -----------------------------------------------------------------------
def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = []
    for m in tar.getmembers():
        parts = Path(m.name).parts
        if not m.isfile() or m.name.startswith("/") or ".." in parts or len(parts) > 2:
            raise PlatformBackupError(f"unexpected entry in archive: {m.name!r}")
        if parts[0] not in ("manifest.json", "tables", "metrics"):
            raise PlatformBackupError(f"unexpected entry in archive: {m.name!r}")
        members.append(m)
    return members


def extract(archive: Path, dest: Path) -> dict[str, Any]:
    """Extract (regular files only, no absolute or parent paths) and return the inner manifest."""
    try:
        with tarfile.open(archive, "r:") as tar:
            for m in _safe_members(tar):
                tar.extract(m, dest, filter="data")
    except tarfile.TarError as exc:
        raise PlatformBackupError(f"not a readable platform archive: {exc}") from exc
    try:
        manifest = json.loads((dest / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        raise PlatformBackupError("the archive has no readable manifest.json") from exc
    if manifest.get("format") != FORMAT:
        raise PlatformBackupError(f"unsupported archive format {manifest.get('format')!r} (expected {FORMAT})")
    return manifest


def verify_contents(dest: Path, manifest: dict[str, Any]) -> list[str]:
    """Problems found comparing the extracted files with the manifest checksums (empty = intact)."""
    problems = []
    for name, t in (manifest.get("tables") or {}).items():
        p = dest / "tables" / f"{name}.jsonl"
        if not p.is_file():
            problems.append(f"tables/{name}.jsonl is missing")
        elif sha256_file(p) != t.get("sha256"):
            problems.append(f"tables/{name}.jsonl checksum mismatch")
    for f in manifest.get("files") or []:
        p = dest / f["path"]
        if not p.is_file():
            problems.append(f"{f['path']} is missing")
        elif sha256_file(p) != f.get("sha256"):
            problems.append(f"{f['path']} checksum mismatch")
    return problems


def read_rows(dest: Path, table: str) -> Iterator[dict[str, Any]]:
    p = dest / "tables" / f"{table}.jsonl"
    if not p.is_file():
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


# ---- compatibility ----------------------------------------------------------------------------
def schema_chain() -> list[str]:
    """Alembic revisions from the first to head (this build)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    return [r.revision for r in reversed(list(script.walk_revisions()))]


def compatibility(manifest: dict[str, Any]) -> dict[str, Any]:
    """``compatible`` (same schema), ``migration_required`` (an older schema of this product:
    restorable, columns added since then take their defaults) or ``unsupported``."""
    from app import __version__

    chain = schema_chain()
    head = chain[-1] if chain else None
    rev = manifest.get("schema_version")
    out: dict[str, Any] = {
        "backup_schema": rev,
        "current_schema": head,
        "backup_version": manifest.get("dbm_version"),
        "current_version": __version__,
        "notes": [],
    }
    if manifest.get("format") != FORMAT:
        return {**out, "status": "unsupported", "notes": [f"archive format {manifest.get('format')!r} is not {FORMAT}"]}
    if rev == head:
        out["status"] = "compatible"
    elif rev in chain:
        out["status"] = "migration_required"
        out["notes"].append(
            f"The backup was taken at schema {rev}; this installation is at {head}. Rows are mapped column by "
            "column: columns added since then take their default values."
        )
    else:
        return {
            **out,
            "status": "unsupported",
            "notes": [
                f"Schema {rev!r} is unknown to this version (the backup was made by a newer or different build). "
                f"Restore it with KBM DBManager {manifest.get('dbm_version')} or later."
            ],
        }
    current = tables()
    for name, t in (manifest.get("tables") or {}).items():
        if name not in current:
            out["notes"].append(f"table {name} no longer exists: its rows are skipped")
            continue
        dropped = sorted(set(t.get("columns") or []) - set(current[name].c.keys()))
        if dropped:
            out["notes"].append(f"{name}: columns {', '.join(dropped)} no longer exist and are skipped")
        missing = [
            c.name
            for c in current[name].columns
            if c.name not in (t.get("columns") or [])
            and not c.nullable
            and c.server_default is None
            and c.default is None
        ]
        if missing:
            out["status"] = "unsupported"
            out["notes"].append(f"{name}: required columns {', '.join(missing)} have no value in the backup")
    return out


# ---- restore ----------------------------------------------------------------------------------
async def _counts(db: AsyncSession) -> dict[str, int]:
    out = {}
    for t in Base.metadata.sorted_tables:
        out[t.name] = int((await db.execute(select(func.count()).select_from(t))).scalar_one())
    return out


def _pk(table: sa.Table) -> list[sa.Column[Any]]:
    return list(table.primary_key.columns)


async def _existing_keys(db: AsyncSession, table: sa.Table) -> set[tuple[Any, ...]]:
    cols = _pk(table)
    return {tuple(r) for r in (await db.execute(select(*cols))).all()}


async def apply(db: AsyncSession, dest: Path, components: list[str]) -> dict[str, Any]:
    """Replace the selected components with the archive's rows inside the caller's transaction.
    Returns per-table inserted/updated/deleted (or merged) counts."""
    names = {t for c in components for t in COMPONENTS[c]}
    current = tables()
    present = [t for t in ordered(names) if t.name in current]
    report: dict[str, dict[str, int]] = {}
    backup_keys: dict[str, set[tuple[Any, ...]]] = {}
    rows_by_table: dict[str, list[dict[str, Any]]] = {}
    for t in present:
        rows = [decode_row(t, r) for r in read_rows(dest, t.name)]
        rows_by_table[t.name] = rows
        backup_keys[t.name] = {tuple(r.get(c.name) for c in _pk(t)) for r in rows}
        before = await _existing_keys(db, t)
        merge = TABLE_COMPONENT[t.name] in MERGE_ONLY
        report[t.name] = {
            "in_backup": len(rows),
            "inserted": len(backup_keys[t.name] - before),
            "updated": 0 if merge else len(backup_keys[t.name] & before),
            "deleted": 0 if merge else len(before - backup_keys[t.name]),
        }
    # 1. delete rows absent from the backup, children first
    for t in reversed(present):
        if TABLE_COMPONENT[t.name] in MERGE_ONLY:
            continue
        pk = _pk(t)
        keep = backup_keys[t.name]
        if not keep:
            await db.execute(sa.delete(t))
        elif len(pk) == 1:
            await db.execute(sa.delete(t).where(sa.not_(pk[0].in_([k[0] for k in keep]))))
        else:
            await db.execute(sa.delete(t).where(sa.not_(sa.tuple_(*pk).in_(list(keep)))))
    # 2. insert or overwrite, parents first
    for t in present:
        rows = rows_by_table[t.name]
        merge = TABLE_COMPONENT[t.name] in MERGE_ONLY
        pk_names = [c.name for c in _pk(t)]
        for i in range(0, len(rows), 500):
            chunk = rows[i : i + 500]
            stmt = pg_insert(t).values(chunk)
            cols = [c for c in chunk[0] if c not in pk_names]
            if merge or not cols:
                stmt = stmt.on_conflict_do_nothing(index_elements=pk_names)
            else:
                stmt = stmt.on_conflict_do_update(
                    index_elements=pk_names, set_={c: getattr(stmt.excluded, c) for c in cols}
                )
            await db.execute(stmt)
    # 3. identity / serial sequences follow the restored ids
    for t in present:
        pk = _pk(t)
        if len(pk) == 1 and isinstance(pk[0].type, sa.Integer | sa.BigInteger):
            await db.execute(
                sa.text(
                    f"SELECT setval(seq, (SELECT COALESCE(MAX({pk[0].name}), 0) + 1 FROM {t.name}), false) "
                    "FROM pg_get_serial_sequence(:tbl, :col) AS seq WHERE seq IS NOT NULL"
                ),
                {"tbl": t.name, "col": pk[0].name},
            )
    await db.flush()
    return report


async def impact(db: AsyncSession, dest: Path, components: list[str]) -> dict[str, Any]:
    """Run :func:`apply` in a SAVEPOINT that is always rolled back; report the changes it would
    make, including rows that cascades would remove from tables outside the selection."""
    before = await _counts(db)
    sp = await db.begin_nested()
    try:
        report = await apply(db, dest, components)
        after = await _counts(db)
        error = None
    except Exception as exc:  # noqa: BLE001 - reported as a blocking problem
        report, after, error = {}, before, f"{type(exc).__name__}: {str(exc).splitlines()[0][:500]}"
    finally:
        await sp.rollback()
    selected = {t for c in components for t in COMPONENTS[c]}
    side_effects = {
        name: after[name] - n for name, n in before.items() if name not in selected and after.get(name, n) != n
    }
    return {"tables": report, "side_effects": side_effects, "error": error}
