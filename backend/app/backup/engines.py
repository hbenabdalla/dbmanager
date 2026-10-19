"""Managed-database backup providers.

``BackupService`` (app.backup.service) → :class:`DatabaseBackupProvider` → engine-specific
implementation. Each provider reports what it can do on a given server
(:meth:`DatabaseBackupProvider.capabilities`) and never claims more:

============  ===============================================  ==============================================
Engine        Backup                                           Restore
============  ===============================================  ==============================================
PostgreSQL    logical, ``pg_dump -Fc`` (copied to the target)  ``pg_restore`` into a **new** database
MySQL         logical, ``mysqldump --single-transaction``      ``mysql`` client into a **new** database
MariaDB       logical, ``mariadb-dump --single-transaction``   ``mariadb`` client into a **new** database
SQL Server    native ``BACKUP DATABASE … COPY_ONLY, CHECKSUM``  ``RESTORE DATABASE … WITH MOVE`` as a **new**
              written on the SQL Server host                   database (``RESTORE VERIFYONLY`` first)
Oracle        Data Pump schema export (``DBMS_DATAPUMP``) to    Data Pump import with ``REMAP_SCHEMA`` into a
              a directory object on the database server        **new** schema. RMAN: external tool required
Db2 LUW       ``ADMIN_CMD('BACKUP DATABASE … ONLINE')`` on the  external tool required (the generated
              Db2 server (needs archive logging)               ``RESTORE DATABASE`` command is shown)
Db2 for z/OS  external tool required (DFSMSdss / COPY utility)  external tool required
============  ===============================================  ==============================================

Restores never overwrite an existing database or schema: the target name must not exist.
Point-in-time recovery is not offered by any provider (logical dumps and copy-only backups do
not form a log chain DBManager manages). Passwords reach external tools only through the
environment (``PGPASSWORD``) or a temporary ``0600`` option file that is deleted afterwards;
they are never put on a command line or written to logs.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core import redact
from app.core.config import get_settings

AVAILABLE = "AVAILABLE"
PARTIAL = "PARTIAL"
REQUIRES_CONFIGURATION = "REQUIRES_CONFIGURATION"
EXTERNAL_TOOL_REQUIRED = "EXTERNAL_TOOL_REQUIRED"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
NOT_SUPPORTED = "NOT_SUPPORTED"
TOOL_MISSING = "TOOL_MISSING"

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#@\-]{0,127}$")
_ORA_IDENT = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")
_SERVER_DIR = re.compile(r"^(?:[A-Za-z]:\\|/)[^'\"*?<>|;\x00-\x1f]{0,400}$")


class BackupProviderError(Exception):
    pass


class Cancelled(BackupProviderError):
    pass


@dataclass
class Context:
    """Everything a provider needs for one asset; built by the service."""

    server: Any
    username: str | None
    password: str | None
    database: str
    workdir: Path
    options: dict[str, Any] = field(default_factory=dict)
    #: adapter factory for the admin credential (engines that work through SQL)
    adapter: Callable[[], Awaitable[Any]] | None = None
    cancelled: Callable[[], Awaitable[bool]] | None = None


@dataclass
class Outcome:
    method: str  # logical | native
    tool: str
    #: a file in the work directory to copy to the storage target, or None for server-side backups
    file: Path | None = None
    #: server-side location (path on the database server, Data Pump directory/file …)
    server_location: str | None = None
    size_bytes: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


def check_identifier(name: str, what: str = "database") -> str:
    if not _IDENT.match(name or ""):
        raise BackupProviderError(f"invalid {what} name {name!r}")
    return name


# ---- external tools ---------------------------------------------------------------------------
def _bin(name: str, setting: str) -> str | None:
    d = getattr(get_settings(), setting, "")
    if d:
        p = Path(d) / name
        return str(p) if p.is_file() and os.access(p, os.X_OK) else None
    return shutil.which(name)


async def _version(path: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            path, "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 10)
    except (OSError, TimeoutError):
        return None
    return out.decode(errors="replace").strip().splitlines()[0][:200] if out else None


_TOOLS = {
    "pg_dump": "backup_pg_bin_dir",
    "pg_restore": "backup_pg_bin_dir",
    "psql": "backup_pg_bin_dir",
    "mysqldump": "backup_mysql_bin_dir",
    "mysql": "backup_mysql_bin_dir",
    "mariadb-dump": "backup_mysql_bin_dir",
    "mariadb": "backup_mysql_bin_dir",
}


async def tools() -> dict[str, dict[str, Any]]:
    """Client tools found on this DBManager host (shown on the engine support page)."""
    out = {}
    for name, setting in _TOOLS.items():
        p = _bin(name, setting)
        out[name] = {"path": p, "version": await _version(p) if p else None}
    return out


def _major(version_text: str | None) -> int | None:
    m = re.search(r"(\d+)(?:\.\d+)", version_text or "")
    return int(m.group(1)) if m else None


async def run_tool(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    stdin: Path | None = None,
    stdout: Path | None = None,
    cancelled: Callable[[], Awaitable[bool]] | None = None,
    secrets: tuple[str | None, ...] = (),
) -> str:
    """Run an external tool; raise with its (redacted) stderr on failure. Returns stderr text."""
    full_env = {k: v for k, v in os.environ.items() if not k.startswith(("DBM_", "PG", "MYSQL"))}
    full_env.update(env or {})
    fin = open(stdin, "rb") if stdin else None  # noqa: SIM115 - closed below
    fout = open(stdout, "wb") if stdout else None  # noqa: SIM115
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=full_env,
            stdin=fin or asyncio.subprocess.DEVNULL,
            stdout=fout or asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        deadline = asyncio.get_running_loop().time() + get_settings().backup_tool_timeout_seconds
        comm = asyncio.ensure_future(proc.communicate())
        while True:
            done, _ = await asyncio.wait({comm}, timeout=2)
            if done:
                break
            if (cancelled and await cancelled()) or asyncio.get_running_loop().time() > deadline:
                proc.kill()
                await comm
                raise Cancelled(f"{Path(argv[0]).name} was stopped (cancelled or timed out)")
        out, err = comm.result()
    finally:
        if fin:
            fin.close()
        if fout:
            fout.close()
    text = (err or b"").decode(errors="replace")
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    text = redact.text(text)
    if proc.returncode != 0:
        tail = "\n".join(text.strip().splitlines()[-8:])
        raise BackupProviderError(f"{Path(argv[0]).name} failed (exit {proc.returncode}): {tail}")
    return text


async def _in_adapter(adapter: Any, fn: Callable[..., Any], *args: Any, timeout: float | None = None) -> Any:  # noqa: ASYNC109 - passed to the adapter thread
    """Run a synchronous engine call in the adapter's worker thread. The adapter turns every
    exception into an ``AdapterError``; backup callers expect ``BackupProviderError`` (with the
    same message) so a failed backup, verification or restore is reported, not crashed on."""
    from app.adapters.base import AdapterError

    try:
        if timeout is None:
            return await adapter._run(fn, *args)
        return await adapter._run(fn, *args, timeout=timeout)
    except AdapterError as exc:
        cause = exc.__cause__
        raise BackupProviderError(str(cause) if isinstance(cause, BackupProviderError) else str(exc)) from exc


def _private_file(directory: Path, name: str, content: str) -> Path:
    p = directory / name
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return p


# ---- base ---------------------------------------------------------------------------------------
class DatabaseBackupProvider:
    engine = ""
    supports_backup = False
    supports_restore = False

    async def capabilities(self, server: Any, found: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            "logical": False,
            "native": False,
            "physical": False,
            "pitr": False,
            "restore": False,
            "external": True,
            "status": EXTERNAL_TOOL_REQUIRED,
            "notes": ["Back this engine up with its own tooling; DBManager does not run it."],
        }

    async def backup(self, ctx: Context) -> Outcome:
        raise BackupProviderError(f"{self.engine}: backups are not performed by DBManager")

    async def verify(self, ctx: Context, backup: Any, local: Path | None) -> dict[str, Any]:
        raise BackupProviderError(f"{self.engine}: no verification available")

    async def restore(self, ctx: Context, backup: Any, local: Path | None, target: str) -> dict[str, Any]:
        raise BackupProviderError(f"{self.engine}: restore is not performed by DBManager")

    async def estimate_size(self, ctx: Context) -> int | None:
        return None

    def restore_command(self, backup: Any, target: str) -> str | None:
        """For EXTERNAL_TOOL_REQUIRED restores: the command an operator runs."""
        return None


# ---- PostgreSQL -------------------------------------------------------------------------------
class PostgreSQLProvider(DatabaseBackupProvider):
    engine = "postgresql"
    supports_backup = supports_restore = True

    async def capabilities(self, server: Any, found: dict[str, dict[str, Any]]) -> dict[str, Any]:
        dump, rest = found.get("pg_dump", {}), found.get("pg_restore", {})
        notes = ["Logical backup with pg_dump (custom format); restore with pg_restore into a new database."]
        status = AVAILABLE
        if not dump.get("path") or not rest.get("path"):
            status = TOOL_MISSING
            notes.append("pg_dump / pg_restore are not installed on the DBManager host (DBM_BACKUP_PG_BIN_DIR).")
        else:
            notes.append(
                f"Client: {dump.get('version')}. pg_dump must be the same major version as the server or newer."
            )
        notes.append("Physical backups (pg_basebackup) and point-in-time recovery are not managed by DBManager.")
        return {
            "logical": True,
            "native": False,
            "physical": False,
            "pitr": False,
            "restore": True,
            "external": False,
            "status": status,
            "notes": notes,
        }

    def _env(self, ctx: Context, database: str) -> dict[str, str]:
        s = ctx.server
        env = {
            "PGHOST": s.host,
            "PGPORT": str(s.port),
            "PGUSER": ctx.username or "",
            "PGDATABASE": database,
            "PGSSLMODE": (s.tls_mode or "prefer").lower(),
            "PGCONNECT_TIMEOUT": "15",
            "PGAPPNAME": "dbmanager-backup",
        }
        if ctx.password:
            env["PGPASSWORD"] = ctx.password
        if s.tls_ca_pem:
            env["PGSSLROOTCERT"] = str(_private_file(ctx.workdir, "pg-ca.pem", s.tls_ca_pem))
        elif env["PGSSLMODE"] in ("verify-ca", "verify-full"):
            env["PGSSLROOTCERT"] = "system"
        return env

    async def backup(self, ctx: Context) -> Outcome:
        found = await tools()
        if not found["pg_dump"]["path"]:
            raise BackupProviderError("pg_dump is not installed on the DBManager host")
        out = ctx.workdir / f"{ctx.database}.dump"
        await run_tool(
            [found["pg_dump"]["path"], "--format=custom", "--no-password", f"--file={out}", "--dbname", ctx.database],
            env=self._env(ctx, ctx.database),
            cancelled=ctx.cancelled,
            secrets=(ctx.password,),
        )
        return Outcome(
            method="logical",
            tool=found["pg_dump"]["version"] or "pg_dump",
            file=out,
            size_bytes=out.stat().st_size,
            details={"format": "pg_dump custom (-Fc)"},
        )

    async def verify(self, ctx: Context, backup: Any, local: Path | None) -> dict[str, Any]:
        if local is None:
            raise BackupProviderError("the dump file is needed for verification")
        found = await tools()
        if not found["pg_restore"]["path"]:
            raise BackupProviderError("pg_restore is not installed on the DBManager host")
        listing = ctx.workdir / "toc.txt"
        await run_tool([found["pg_restore"]["path"], "--list", str(local)], stdout=listing)
        entries = [ln for ln in listing.read_text(errors="replace").splitlines() if ln and not ln.startswith(";")]
        if not entries:
            raise BackupProviderError("pg_restore --list found no objects in the dump")
        tables = sum(1 for e in entries if " TABLE " in e and " TABLE DATA " not in e)
        return {"level": "engine", "tool": "pg_restore --list", "toc_entries": len(entries), "tables": tables}

    async def restore(self, ctx: Context, backup: Any, local: Path | None, target: str) -> dict[str, Any]:
        check_identifier(target)
        if local is None:
            raise BackupProviderError("the dump file is needed for restore")
        found = await tools()
        if not found["pg_restore"]["path"]:
            raise BackupProviderError("pg_restore is not installed on the DBManager host")
        assert ctx.adapter is not None
        adapter = await ctx.adapter()
        try:
            conn = await adapter._conn(None)
            if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", target):
                raise BackupProviderError(f"database {target!r} already exists: restores never overwrite a database")
            server_major = int(await conn.fetchval("SHOW server_version_num")) // 10000
            await conn.execute(f'CREATE DATABASE "{target}"')
        finally:
            await adapter.close()
        tool_major = _major(found["pg_restore"]["version"]) or server_major
        env = self._env(ctx, target)
        owner = ["--no-owner"] if ctx.options.get("no_owner", True) else []
        if tool_major > server_major:
            # A newer pg_restore writes session settings the older server does not know (pg_restore 17
            # emits "SET transaction_timeout", unknown before PostgreSQL 17). Render the restore script,
            # drop exactly those settings and run it with psql, stopping at the first error.
            if not found.get("psql", {}).get("path"):
                raise BackupProviderError("psql is needed to restore into an older PostgreSQL server")
            script = ctx.workdir / "restore.sql"
            await run_tool([found["pg_restore"]["path"], *owner, "--file", str(script), str(local)])
            dropped = filter_settings(script, server_major)
            await run_tool(
                [
                    found["psql"]["path"],
                    "--no-psqlrc",
                    "--quiet",
                    "--set",
                    "ON_ERROR_STOP=1",
                    "--dbname",
                    target,
                    "--file",
                    str(script),
                ],
                env=env,
                cancelled=ctx.cancelled,
                secrets=(ctx.password,),
                stdout=ctx.workdir / "psql.out",
            )
            return {"database": target, "tool": f"{found['pg_restore']['version']} → psql", "settings_dropped": dropped}
        argv = [found["pg_restore"]["path"], *owner, "--no-password", "--exit-on-error", "--dbname", target, str(local)]
        await run_tool(argv, env=env, cancelled=ctx.cancelled, secrets=(ctx.password,))
        return {"database": target, "tool": found["pg_restore"]["version"]}

    async def estimate_size(self, ctx: Context) -> int | None:
        if ctx.adapter is None:
            return None
        adapter = await ctx.adapter()
        try:
            conn = await adapter._conn(None)
            return int(await conn.fetchval("SELECT pg_database_size($1)", ctx.database) or 0)
        finally:
            await adapter.close()


#: session settings newer pg_dump / pg_restore versions emit, by the server version that introduced them
PG_NEW_SETTINGS: dict[str, int] = {"transaction_timeout": 17}


def filter_settings(script: Path, server_major: int) -> list[str]:
    """Remove top-level ``SET <name> = …;`` lines for settings the target server does not have yet.
    Only exact, whole-line SET statements of the names in :data:`PG_NEW_SETTINGS` are touched."""
    unknown = {n for n, v in PG_NEW_SETTINGS.items() if server_major < v}
    if not unknown:
        return []
    pattern = re.compile(r"^SET (" + "|".join(sorted(unknown)) + r") = [^;]*;\s*$")
    tmp = script.with_suffix(".filtered")
    dropped: list[str] = []
    with (
        open(script, encoding="utf-8", errors="surrogateescape") as src,
        open(tmp, "w", encoding="utf-8", errors="surrogateescape") as dst,
    ):
        for line in src:
            if pattern.match(line):
                dropped.append(line.strip())
                continue
            dst.write(line)
    os.replace(tmp, script)
    return dropped


# ---- MySQL / MariaDB ------------------------------------------------------------------------------
class MySQLFamilyProvider(DatabaseBackupProvider):
    engine = "mysql"
    supports_backup = supports_restore = True
    dump_tools = ("mysqldump", "mariadb-dump")
    client_tools = ("mysql", "mariadb")

    def _pick(self, found: dict[str, dict[str, Any]], names: tuple[str, ...]) -> tuple[str, str] | None:
        for n in names:
            if found.get(n, {}).get("path"):
                return found[n]["path"], found[n]["version"] or n
        return None

    async def capabilities(self, server: Any, found: dict[str, dict[str, Any]]) -> dict[str, Any]:
        dump, client = self._pick(found, self.dump_tools), self._pick(found, self.client_tools)
        status, notes = AVAILABLE, []
        label = "mariadb-dump" if self.engine == "mariadb" else "mysqldump"
        notes.append(
            f"Logical backup with {label} --single-transaction (InnoDB-consistent), including routines, "
            "triggers and events, utf8mb4; restore into a new database with the command-line client."
        )
        if not dump or not client:
            status = TOOL_MISSING
            notes.append("The dump / client tools are not installed on the DBManager host (DBM_BACKUP_MYSQL_BIN_DIR).")
        elif self.engine == "mysql" and "mariadb" in dump[1].lower():
            status = PARTIAL
            notes.append("Only the MariaDB dump client is installed: MySQL-specific objects may not round-trip.")
        notes.append("Non-transactional (MyISAM) tables are not consistent under --single-transaction.")
        notes.append("Physical backups (MySQL Enterprise Backup, mariabackup) and binlog PITR are not managed.")
        return {
            "logical": True,
            "native": False,
            "physical": False,
            "pitr": False,
            "restore": True,
            "external": False,
            "status": status,
            "notes": notes,
        }

    def _defaults(self, ctx: Context, flavor: str) -> Path:
        s = ctx.server
        lines = ["[client]", f"host={s.host}", f"port={s.port}", f"user={ctx.username or ''}"]
        if ctx.password:
            lines.append("password=" + ctx.password.replace("\\", "\\\\").replace('"', '\\"').join('""'))
        mode = (s.tls_mode or "prefer").lower()
        ca = _private_file(ctx.workdir, "mysql-ca.pem", s.tls_ca_pem) if s.tls_ca_pem else None
        if flavor == "mysql":
            lines.append(
                "ssl-mode="
                + {
                    "disable": "DISABLED",
                    "allow": "PREFERRED",
                    "prefer": "PREFERRED",
                    "require": "REQUIRED",
                    "verify-ca": "VERIFY_CA",
                    "verify-full": "VERIFY_IDENTITY",
                }.get(mode, "PREFERRED")
            )
        elif mode == "disable":
            lines.append("skip-ssl")
        else:
            lines.append("ssl")
            lines.append(
                "ssl-verify-server-cert" if mode in ("verify-ca", "verify-full") else "disable-ssl-verify-server-cert"
            )
        if ca:
            lines.append(f"ssl-ca={ca}")
        return _private_file(ctx.workdir, "client.cnf", "\n".join(lines) + "\n")

    async def backup(self, ctx: Context) -> Outcome:
        check_identifier(ctx.database)
        picked = self._pick(await tools(), self.dump_tools)
        if not picked:
            raise BackupProviderError("no mysqldump / mariadb-dump on the DBManager host")
        path, version = picked
        flavor = "mariadb" if "mariadb" in version.lower() else "mysql"
        defaults = self._defaults(ctx, flavor)
        out = ctx.workdir / f"{ctx.database}.sql"
        argv = [
            path,
            f"--defaults-extra-file={defaults}",
            "--single-transaction",
            "--routines",
            "--triggers",
            "--events",
            "--hex-blob",
            "--no-tablespaces",
            "--default-character-set=utf8mb4",
            f"--result-file={out}",
        ]
        if flavor == "mysql":
            argv.append("--set-gtid-purged=OFF")  # restorable on another server / into a new name
        argv.append(ctx.database)  # one database without --databases: no CREATE DATABASE / USE
        try:
            await run_tool(argv, cancelled=ctx.cancelled, secrets=(ctx.password,))
        finally:
            defaults.unlink(missing_ok=True)
        return Outcome(
            method="logical",
            tool=version,
            file=out,
            size_bytes=out.stat().st_size,
            details={"options": "--single-transaction --routines --triggers --events --hex-blob utf8mb4"},
        )

    async def verify(self, ctx: Context, backup: Any, local: Path | None) -> dict[str, Any]:
        if local is None:
            raise BackupProviderError("the dump file is needed for verification")
        size = local.stat().st_size
        with open(local, "rb") as f:
            f.seek(max(0, size - 4096))
            tail = f.read().decode(errors="replace")
            f.seek(0)
            head = f.read(4096).decode(errors="replace")
        if "-- Dump completed" not in tail:
            raise BackupProviderError("the dump is incomplete (no '-- Dump completed' trailer)")
        if not re.search(r"-- (MySQL|MariaDB) dump", head):
            raise BackupProviderError("the file is not a mysqldump / mariadb-dump output")
        return {"level": "engine", "check": "dump header and completion trailer", "size": size}

    async def restore(self, ctx: Context, backup: Any, local: Path | None, target: str) -> dict[str, Any]:
        check_identifier(target)
        if local is None:
            raise BackupProviderError("the dump file is needed for restore")
        source = str(getattr(backup, "database_name", "") or "")
        if source and source != target and _mentions_schema(local, source):
            # a definition qualified with the source name would be (re)created in the SOURCE database
            raise BackupProviderError(
                f"the dump contains objects qualified with the source database name `{source}` (e.g. triggers or "
                "routines created as `db`.name): restoring it under another name would change the source database. "
                "Restore it on another server under the same name, or edit the definitions first."
            )
        picked = self._pick(await tools(), self.client_tools)
        if not picked:
            raise BackupProviderError("no mysql / mariadb client on the DBManager host")
        path, version = picked
        flavor = "mariadb" if "mariadb" in version.lower() else "mysql"
        defaults = self._defaults(ctx, flavor)
        try:
            exists = ctx.workdir / "exists.txt"
            await run_tool(
                [
                    path,
                    f"--defaults-extra-file={defaults}",
                    "--batch",
                    "--skip-column-names",
                    "-e",
                    f"SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = '{target}'",
                ],
                stdout=exists,
                secrets=(ctx.password,),
            )
            if exists.read_text().strip() not in ("0", ""):
                raise BackupProviderError(f"database {target!r} already exists: restores never overwrite a database")
            programs = stored_programs(local) & BINLOG_GUARDED
            if programs:
                await self._binlog_preflight(path, defaults, ctx, programs)
            await run_tool(
                [
                    path,
                    f"--defaults-extra-file={defaults}",
                    "-e",
                    f"CREATE DATABASE `{target}` CHARACTER SET utf8mb4",
                ],
                secrets=(ctx.password,),
            )
            script = local
            stripped = 0
            if not ctx.options.get("keep_definers"):
                # objects come back owned by the restoring account: restoring another account's
                # DEFINER would need SUPER / SET_USER_ID, which a backup operator should not hold
                script = ctx.workdir / "restore.sql"
                stripped = strip_definers(local, script)
            try:
                await run_tool(
                    [path, f"--defaults-extra-file={defaults}", "--default-character-set=utf8mb4", target],
                    stdin=script,
                    cancelled=ctx.cancelled,
                    secrets=(ctx.password,),
                )
            except BackupProviderError as exc:
                if "ERROR 1419" in str(exc):
                    raise BackupProviderError(f"{exc}\n{BINLOG_HINT}") from exc
                raise
        finally:
            defaults.unlink(missing_ok=True)
        return {"database": target, "tool": version, "definers_removed": stripped}

    async def _binlog_preflight(self, client: str, defaults: Path, ctx: Context, programs: set[str]) -> None:
        """With binary logging on, creating triggers and routines needs SUPER unless the server
        trusts routine creators (error 1419). Refuse before anything is created, with the fix."""
        out = ctx.workdir / "binlog.txt"
        await run_tool(
            [
                client,
                f"--defaults-extra-file={defaults}",
                "--batch",
                "--skip-column-names",
                "-e",
                BINLOG_QUERY,
            ],
            stdout=out,
            secrets=(ctx.password,),
        )
        verdict = binlog_blocks(out.read_text())
        if verdict:
            kinds = ", ".join(sorted(k.lower() + "s" for k in programs))
            raise BackupProviderError(
                f"the dump contains {kinds}, and the target server has binary logging on with "
                "log_bin_trust_function_creators = OFF: the restoring account would need SUPER (error 1419). "
                f"Nothing was created. {BINLOG_HINT}"
            )


_DEFINER = re.compile(
    rb"DEFINER\s*=\s*(?:`(?:[^`]|``)*`|'[^']*'|[A-Za-z0-9_.%-]+)@(?:`(?:[^`]|``)*`|'[^']*'|[A-Za-z0-9_.%-]+)\s*"
)


def strip_definers(src: Path, dst: Path) -> int:
    """Copy a SQL dump without ``DEFINER=user@host`` clauses (also inside ``/*!50017 … */``).
    Returns the number of clauses removed. Line by line: dumps can be large."""
    n = 0
    with open(src, "rb") as r, open(dst, "wb") as w:
        for line in r:
            if b"DEFINER" in line:
                line, k = _DEFINER.subn(b"", line)
                n += k
            w.write(line)
    return n


#: stored programs whose creation is restricted by binary logging (MySQL error 1419)
BINLOG_GUARDED = {"TRIGGER", "PROCEDURE", "FUNCTION"}
BINLOG_HINT = (
    "Either a DBA sets log_bin_trust_function_creators = ON on the target server (SET PERSIST "
    "log_bin_trust_function_creators = ON; see the MySQL documentation on binary logging of stored "
    "programs), or the restore runs with an account that holds SUPER."
)
#: binary logging, routine-creator trust and whether the current account holds SUPER
BINLOG_QUERY = (
    "SELECT @@global.log_bin, @@global.log_bin_trust_function_creators, "
    "(SELECT COUNT(*) FROM information_schema.user_privileges WHERE privilege_type = 'SUPER' AND grantee = "
    "CONCAT(CHAR(39), SUBSTRING_INDEX(CURRENT_USER(), '@', 1), CHAR(39), '@', CHAR(39), "
    "SUBSTRING_INDEX(CURRENT_USER(), '@', -1), CHAR(39)))"
)
_PROGRAM = re.compile(rb"^(?:/\*!\d+ )?CREATE\b.*?\b(TRIGGER|PROCEDURE|FUNCTION|EVENT)\b")


def binlog_blocks(output: str) -> bool:
    """Whether the ``BINLOG_QUERY`` result means stored programs cannot be created (binary
    logging on, creators not trusted, no SUPER). Unexpected output → ``False``: the restore then
    reports the server's own error."""
    fields = output.split()
    if len(fields) != 3:
        return False
    log_bin, trusted, has_super = (f.strip().upper() for f in fields)
    return log_bin in ("1", "ON") and trusted in ("0", "OFF") and has_super == "0"


def stored_programs(path: Path) -> set[str]:
    """Kinds of stored programs a mysqldump / mariadb-dump file creates (triggers, routines,
    events), from the CREATE statements at the start of a line."""
    found: set[str] = set()
    with open(path, "rb") as f:
        for line in f:
            if line.startswith((b"CREATE", b"/*!")) and (m := _PROGRAM.match(line)):
                found.add(m.group(1).decode())
    return found


def _mentions_schema(path: Path, schema: str) -> bool:
    """Whether a SQL dump references ```schema`.`` outside its own header comments."""
    needle = f"`{schema}`.".encode()
    tail = b""
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            if needle in tail + chunk:
                return True
            tail = chunk[-len(needle) :]
    return False


class MariaDBProvider(MySQLFamilyProvider):
    engine = "mariadb"
    dump_tools = ("mariadb-dump", "mysqldump")
    client_tools = ("mariadb", "mysql")


# ---- SQL Server (native, server-side) -----------------------------------------------------------
def _nq(value: str) -> str:
    """N'…' literal with quotes doubled."""
    return "N'" + value.replace("'", "''") + "'"


def _bracket(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def _drain(cur: Any) -> list[list[Any]]:
    """BACKUP/RESTORE report progress as messages; reading every result set waits for completion."""
    rows: list[list[Any]] = []
    while True:
        if cur.description:
            rows += [list(r) for r in cur.fetchall()]
        if not cur.nextset():
            return rows


class SQLServerProvider(DatabaseBackupProvider):
    engine = "mssql"
    supports_backup = supports_restore = True

    async def capabilities(self, server: Any, found: dict[str, dict[str, Any]]) -> dict[str, Any]:
        if server.deployment == "azure-sql" or "database.windows.net" in (server.host or ""):
            return {
                "logical": False,
                "native": False,
                "physical": False,
                "pitr": False,
                "restore": False,
                "external": True,
                "status": NOT_SUPPORTED,
                "notes": ["Azure SQL Database backups are managed by Azure (automated backups / export to BACPAC)."],
            }
        return {
            "logical": False,
            "native": True,
            "physical": False,
            "pitr": False,
            "restore": True,
            "external": False,
            "status": AVAILABLE,
            "notes": [
                "Native BACKUP DATABASE … WITH COPY_ONLY, CHECKSUM (does not disturb the log chain of your own "
                "backup schedule). The .bak file is written by SQL Server on its own host or container, in the "
                "instance default backup directory or a validated directory.",
                "Copy the file to protected storage with your file-level backup; DBManager records and verifies it.",
                "Restore: RESTORE VERIFYONLY, then RESTORE DATABASE as a new database with MOVE of every file.",
                "Needs sysadmin or db_backupoperator (backup) and dbcreator (restore) for the administration login.",
            ],
        }

    async def _call(self, ctx: Context, fn: Callable[..., Any], *args: Any) -> Any:
        assert ctx.adapter is not None
        adapter = await ctx.adapter()
        try:
            conn = await _in_adapter(adapter, adapter._get_conn, "master")
            conn.autocommit = True  # BACKUP / RESTORE cannot run inside a transaction
            return await _in_adapter(
                adapter, fn, conn, *args, timeout=float(get_settings().backup_tool_timeout_seconds)
            )
        finally:
            await adapter.close()

    @staticmethod
    def _directory(conn: Any, wanted: str | None) -> str:
        if wanted:
            if not _SERVER_DIR.match(wanted):
                raise BackupProviderError(f"invalid server directory {wanted!r}")
            return wanted.rstrip("\\/")
        cur = conn.cursor()
        cur.execute("SELECT CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS nvarchar(512))")
        row = cur.fetchone()
        if not row or not row[0]:
            raise BackupProviderError(
                "SQL Server does not report a default backup directory (before 2019): set a server directory"
            )
        return str(row[0]).rstrip("\\/")

    def _backup_sync(self, conn: Any, database: str, wanted_dir: str | None, tag: str) -> dict[str, Any]:
        directory = self._directory(conn, wanted_dir)
        sep = "\\" if re.match(r"^[A-Za-z]:\\", directory) else "/"
        path = f"{directory}{sep}{database}_{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{tag}.bak"
        cur = conn.cursor()
        cur.execute("SELECT DB_ID(?)", database)
        if cur.fetchone()[0] is None:
            raise BackupProviderError(f"database {database!r} does not exist")
        cur.execute(
            f"BACKUP DATABASE {_bracket(database)} TO DISK = {_nq(path)} "
            f"WITH COPY_ONLY, CHECKSUM, INIT, FORMAT, NAME = {_nq('DBManager ' + tag)}"
        )
        _drain(cur)
        # read the header of the file just written (db_backupoperator / dbcreator may not read msdb)
        cur.execute(f"RESTORE HEADERONLY FROM DISK = {_nq(path)}")
        cols = [str(d[0]).lower() for d in cur.description or []]
        rows = _drain(cur)
        h = dict(zip(cols, rows[0], strict=False)) if rows else {}
        size = h.get("compressedbackupsize") or h.get("backupsize")
        return {
            "path": path,
            "size": int(size) if size is not None else None,
            "checksums": bool(h.get("hasbackupchecksums")) if "hasbackupchecksums" in h else None,
            "finished": str(h.get("backupfinishdate")) if h.get("backupfinishdate") is not None else None,
        }

    async def backup(self, ctx: Context) -> Outcome:
        check_identifier(ctx.database)
        tag = str(ctx.options.get("tag") or "dbm")[:12]
        info = await self._call(ctx, self._backup_sync, ctx.database, ctx.options.get("server_directory"), tag)
        return Outcome(
            method="native",
            tool="BACKUP DATABASE (COPY_ONLY, CHECKSUM)",
            server_location=info["path"],
            size_bytes=info["size"],
            details={"checksums": info["checksums"], "finished": info["finished"]},
        )

    @staticmethod
    def _verify_sync(conn: Any, path: str) -> dict[str, Any]:
        cur = conn.cursor()
        cur.execute(f"RESTORE VERIFYONLY FROM DISK = {_nq(path)} WITH CHECKSUM")
        _drain(cur)
        return {"level": "engine", "tool": "RESTORE VERIFYONLY WITH CHECKSUM", "path": path}

    async def verify(self, ctx: Context, backup: Any, local: Path | None) -> dict[str, Any]:
        return await self._call(ctx, self._verify_sync, backup.location)

    @staticmethod
    def _restore_sync(conn: Any, path: str, target: str) -> dict[str, Any]:
        cur = conn.cursor()
        cur.execute("SELECT DB_ID(?)", target)
        if cur.fetchone()[0] is not None:
            raise BackupProviderError(f"database {target!r} already exists: restores never overwrite a database")
        cur.execute(f"RESTORE VERIFYONLY FROM DISK = {_nq(path)} WITH CHECKSUM")
        _drain(cur)
        cur.execute(f"RESTORE FILELISTONLY FROM DISK = {_nq(path)}")
        files = [(r[0], r[2]) for r in _drain(cur)]  # LogicalName, Type (D data, L log, S filestream)
        cur.execute(
            "SELECT CAST(SERVERPROPERTY('InstanceDefaultDataPath') AS nvarchar(512)), "
            "CAST(SERVERPROPERTY('InstanceDefaultLogPath') AS nvarchar(512))"
        )
        data_dir, log_dir = cur.fetchone()
        if not data_dir or not log_dir:
            raise BackupProviderError("SQL Server does not report default data/log directories")
        sep = "\\" if re.match(r"^[A-Za-z]:\\", str(data_dir)) else "/"
        moves = []
        for i, (logical, kind) in enumerate(files):
            base = str(log_dir if kind == "L" else data_dir).rstrip("\\/")
            ext = ".ldf" if kind == "L" else (".mdf" if i == 0 else ".ndf")
            moves.append(f"MOVE {_nq(logical)} TO {_nq(f'{base}{sep}{target}_{i}{ext}')}")
        cur.execute(
            f"RESTORE DATABASE {_bracket(target)} FROM DISK = {_nq(path)} WITH {', '.join(moves)}, CHECKSUM, RECOVERY"
        )
        _drain(cur)
        return {"database": target, "files": len(files)}

    async def restore(self, ctx: Context, backup: Any, local: Path | None, target: str) -> dict[str, Any]:
        check_identifier(target)
        return await self._call(ctx, self._restore_sync, backup.location, target)


# ---- Oracle (Data Pump, server-side) -------------------------------------------------------------
_DP_EXPORT = """
DECLARE
  h NUMBER;
  st VARCHAR2(30);
BEGIN
  h := DBMS_DATAPUMP.OPEN(operation => 'EXPORT', job_mode => 'SCHEMA', job_name => :job);
  DBMS_DATAPUMP.ADD_FILE(h, :dumpfile, :dir);
  DBMS_DATAPUMP.ADD_FILE(h, :logfile, :dir, NULL, DBMS_DATAPUMP.KU$_FILE_TYPE_LOG_FILE);
  DBMS_DATAPUMP.METADATA_FILTER(h, 'SCHEMA_LIST', '''' || :schema_name || '''');
  DBMS_DATAPUMP.SET_PARAMETER(h, 'FLASHBACK_TIME', 'SYSTIMESTAMP');
  DBMS_DATAPUMP.START_JOB(h);
  DBMS_DATAPUMP.WAIT_FOR_JOB(h, st);
  :state := st;
END;"""
_DP_IMPORT = """
DECLARE
  h NUMBER;
  st VARCHAR2(30);
BEGIN
  h := DBMS_DATAPUMP.OPEN(operation => 'IMPORT', job_mode => 'SCHEMA', job_name => :job);
  DBMS_DATAPUMP.ADD_FILE(h, :dumpfile, :dir);
  DBMS_DATAPUMP.ADD_FILE(h, :logfile, :dir, NULL, DBMS_DATAPUMP.KU$_FILE_TYPE_LOG_FILE);
  DBMS_DATAPUMP.METADATA_REMAP(h, 'REMAP_SCHEMA', :source_schema, :target_schema);
  DBMS_DATAPUMP.SET_PARAMETER(h, 'TABLE_EXISTS_ACTION', 'SKIP');
  DBMS_DATAPUMP.START_JOB(h);
  DBMS_DATAPUMP.WAIT_FOR_JOB(h, st);
  :state := st;
END;"""
_DP_INFO = """
DECLARE
  info ku$_dumpfile_info;
  ft NUMBER;
BEGIN
  DBMS_DATAPUMP.GET_DUMPFILE_INFO(:dumpfile, :dir, info, ft);
  :filetype := ft;
END;"""


class OracleProvider(DatabaseBackupProvider):
    engine = "oracle"
    supports_backup = supports_restore = True

    async def capabilities(self, server: Any, found: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            "logical": True,
            "native": False,
            "physical": False,
            "pitr": False,
            "restore": True,
            "external": True,
            "status": AVAILABLE,
            "notes": [
                "Logical schema export with Data Pump (DBMS_DATAPUMP) into a directory object on the database "
                "server (default DATA_PUMP_DIR), consistent as of the start (FLASHBACK_TIME).",
                "Restore: Data Pump import with REMAP_SCHEMA into a new schema.",
                "No Data Pump compression or encryption options are used (they need the Advanced Compression / "
                "Advanced Security options).",
                "Physical backups and point-in-time recovery need RMAN: external tool required, not run by DBManager.",
                "Needs the DATAPUMP_EXP_FULL_DATABASE / DATAPUMP_IMP_FULL_DATABASE roles (or the schema owner) "
                "and READ, WRITE on the directory object.",
            ],
        }

    async def _call(self, ctx: Context, fn: Callable[..., Any], *args: Any) -> Any:
        assert ctx.adapter is not None
        adapter = await ctx.adapter()
        try:
            conn = await _in_adapter(adapter, adapter._get_conn, None)
            return await _in_adapter(
                adapter, fn, conn, *args, timeout=float(get_settings().backup_tool_timeout_seconds)
            )
        finally:
            await adapter.close()

    @staticmethod
    def _export_sync(conn: Any, schema: str, directory: str, tag: str) -> dict[str, Any]:
        cur = conn.cursor()
        state = cur.var(str)
        dump = f"DBM_{schema}_{tag}.dmp".upper()
        cur.execute(
            _DP_EXPORT,
            job=f"DBM_EXP_{tag}".upper()[:30],
            dumpfile=dump,
            logfile=dump.replace(".DMP", ".LOG"),
            dir=directory,
            schema_name=schema,
            state=state,
        )
        if (state.getvalue() or "").upper() != "COMPLETED":
            raise BackupProviderError(f"Data Pump export ended in state {state.getvalue()!r} (see {dump[:-4]}.LOG)")
        return {"dumpfile": dump, "directory": directory}

    async def backup(self, ctx: Context) -> Outcome:
        schema = ctx.database.upper()
        if not _ORA_IDENT.match(schema):
            raise BackupProviderError(f"invalid schema name {ctx.database!r}")
        directory = str(ctx.options.get("directory") or "DATA_PUMP_DIR").upper()
        if not _ORA_IDENT.match(directory):
            raise BackupProviderError(f"invalid directory object {directory!r}")
        tag = str(ctx.options.get("tag") or "x")[:12]
        info = await self._call(ctx, self._export_sync, schema, directory, tag)
        return Outcome(
            method="logical",
            tool="Oracle Data Pump (DBMS_DATAPUMP, schema mode)",
            server_location=f"{info['directory']}:{info['dumpfile']}",
            details=info,
        )

    @staticmethod
    def _info_sync(conn: Any, directory: str, dumpfile: str) -> dict[str, Any]:
        cur = conn.cursor()
        ft = cur.var(int)
        cur.execute(_DP_INFO, dumpfile=dumpfile, dir=directory, filetype=ft)
        if not ft.getvalue():
            raise BackupProviderError(f"{dumpfile} is not a valid Data Pump dump file")
        return {"level": "engine", "tool": "DBMS_DATAPUMP.GET_DUMPFILE_INFO", "filetype": ft.getvalue()}

    async def verify(self, ctx: Context, backup: Any, local: Path | None) -> dict[str, Any]:
        directory, _, dump = str(backup.location).partition(":")
        return await self._call(ctx, self._info_sync, directory, dump)

    @staticmethod
    def _import_sync(conn: Any, directory: str, dump: str, source: str, target: str, tag: str) -> dict[str, Any]:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM all_users WHERE username = :u", u=target)
        if cur.fetchone()[0]:
            raise BackupProviderError(f"schema {target!r} already exists: restores never overwrite a schema")
        state = cur.var(str)
        cur.execute(
            _DP_IMPORT,
            job=f"DBM_IMP_{tag}".upper()[:30],
            dumpfile=dump,
            logfile=f"DBM_IMP_{target}_{tag}.LOG"[:60],
            dir=directory,
            source_schema=source,
            target_schema=target,
            state=state,
        )
        if (state.getvalue() or "").upper() != "COMPLETED":
            raise BackupProviderError(f"Data Pump import ended in state {state.getvalue()!r}")
        return {"schema": target}

    async def restore(self, ctx: Context, backup: Any, local: Path | None, target: str) -> dict[str, Any]:
        target = target.upper()
        if not _ORA_IDENT.match(target):
            raise BackupProviderError(f"invalid schema name {target!r}")
        directory, _, dump = str(backup.location).partition(":")
        source = str(backup.database_name or "").upper()
        tag = str(ctx.options.get("tag") or "x")[:12]
        return await self._call(ctx, self._import_sync, directory, dump, source, target, tag)


# ---- Db2 LUW -----------------------------------------------------------------------------------
class Db2LUWProvider(DatabaseBackupProvider):
    engine = "db2"
    supports_backup = True
    supports_restore = False

    async def capabilities(self, server: Any, found: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            "logical": False,
            "native": True,
            "physical": True,
            "pitr": False,
            "restore": False,
            "external": True,
            "status": AVAILABLE,
            "notes": [
                "Online database backup with ADMIN_CMD('BACKUP DATABASE … ONLINE TO <dir> INCLUDE LOGS') on the "
                "Db2 server; needs archive logging (LOGARCHMETH1 not OFF) — otherwise REQUIRES_CONFIGURATION.",
                "Verification reads the recovery history (SYSIBMADM.DB_HISTORY); a full image check needs "
                "db2ckbkp on the Db2 host (external tool).",
                "Restore: external tool required — DBManager shows the RESTORE DATABASE command to run on the "
                "Db2 host. Rollforward / point-in-time recovery is not managed.",
                "Applies to Db2 for Linux, UNIX and Windows only (not Db2 for z/OS).",
            ],
        }

    @staticmethod
    def _sync_backup(conn: Any, database: str, directory: str) -> dict[str, Any]:
        import ibm_db_dbi  # noqa: F401 - the adapter's driver

        cur = conn.cursor()
        cur.execute("SELECT VALUE FROM SYSIBMADM.DBCFG WHERE NAME = 'logarchmeth1'")
        r = cur.fetchone()
        if not r or str(r[0]).strip().upper() == "OFF":
            raise BackupProviderError(
                "REQUIRES_CONFIGURATION: online backups need archive logging (UPDATE DB CFG USING LOGARCHMETH1 …)"
            )
        cur.execute(f"CALL SYSPROC.ADMIN_CMD('BACKUP DATABASE {database} ONLINE TO {directory} INCLUDE LOGS')")
        row = cur.fetchone()
        conn.commit()
        return {"timestamp": str(row[0]).strip() if row else None}

    async def backup(self, ctx: Context) -> Outcome:
        database = check_identifier(ctx.database).upper()
        directory = str(ctx.options.get("server_directory") or "")
        if not _SERVER_DIR.match(directory) or "'" in directory:
            raise BackupProviderError("set a server directory (absolute path on the Db2 server) for Db2 backups")
        assert ctx.adapter is not None
        adapter = await ctx.adapter()
        try:
            conn = await _in_adapter(adapter, adapter._get_conn, None)
            info = await _in_adapter(
                adapter,
                self._sync_backup,
                conn,
                database,
                directory,
                timeout=float(get_settings().backup_tool_timeout_seconds),
            )
        finally:
            await adapter.close()
        return Outcome(
            method="native",
            tool="ADMIN_CMD BACKUP DATABASE ONLINE",
            server_location=f"{directory}#{info['timestamp']}",
            details=info,
        )

    @staticmethod
    def _sync_history(conn: Any, timestamp: str) -> dict[str, Any]:
        cur = conn.cursor()
        cur.execute(
            "SELECT START_TIME, END_TIME, SQLCODE, LOCATION FROM SYSIBMADM.DB_HISTORY "
            "WHERE OPERATION = 'B' AND START_TIME = ?",
            (timestamp,),
        )
        r = cur.fetchone()
        if not r:
            raise BackupProviderError(f"no backup taken at {timestamp} in the recovery history")
        if r[2] not in (None, 0):
            raise BackupProviderError(f"the recovery history records SQLCODE {r[2]} for this backup")
        return {"level": "engine", "tool": "SYSIBMADM.DB_HISTORY", "end_time": str(r[1]), "location": str(r[3])}

    async def verify(self, ctx: Context, backup: Any, local: Path | None) -> dict[str, Any]:
        _, _, ts = str(backup.location).partition("#")
        assert ctx.adapter is not None
        adapter = await ctx.adapter()
        try:
            conn = await _in_adapter(adapter, adapter._get_conn, None)
            return await _in_adapter(adapter, self._sync_history, conn, ts)
        finally:
            await adapter.close()

    def restore_command(self, backup: Any, target: str) -> str | None:
        """Commands for the instance owner on the Db2 host: restore the online image (with the logs
        it includes) as a new database and roll forward to the end of the backup."""
        directory, _, ts = str(backup.location).partition("#")
        logs = f"{directory.rstrip('/')}/dbm-restore-logs-{ts}"
        return (
            f"mkdir -p {logs}\n"
            f"db2 RESTORE DATABASE {backup.database_name} FROM {directory} TAKEN AT {ts} "
            f"INTO {target} LOGTARGET {logs} WITHOUT PROMPTING\n"
            f"db2 ROLLFORWARD DATABASE {target} TO END OF BACKUP AND COMPLETE OVERFLOW LOG PATH ({logs})"
        )


class Db2ZOSProvider(DatabaseBackupProvider):
    engine = "db2zos"

    async def capabilities(self, server: Any, found: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            "logical": False,
            "native": False,
            "physical": False,
            "pitr": False,
            "restore": False,
            "external": True,
            "status": EXTERNAL_TOOL_REQUIRED,
            "notes": [
                "Db2 for z/OS is backed up with z/OS utilities (COPY / RECOVER, DFSMSdss, BACKUP SYSTEM) under "
                "your site's job scheduler. DBManager does not run them (NOT IMPLEMENTED).",
            ],
        }


PROVIDERS: dict[str, DatabaseBackupProvider] = {
    p.engine: p
    for p in (
        PostgreSQLProvider(),
        MySQLFamilyProvider(),
        MariaDBProvider(),
        SQLServerProvider(),
        OracleProvider(),
        Db2LUWProvider(),
        Db2ZOSProvider(),
    )
}


def provider(engine: str) -> DatabaseBackupProvider:
    p = PROVIDERS.get(engine)
    if p is None:
        p = DatabaseBackupProvider()
        p.engine = engine
    return p


async def capabilities(server: Any, found: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    found = found if found is not None else await tools()
    caps = await provider(server.engine).capabilities(server, found)
    if server.is_demo:
        caps = {**caps, "status": NOT_SUPPORTED, "notes": ["Simulated servers have no database to back up."]}
    return caps
