"""MySQL-family connection management and discovery (PyMySQL).

Shared by :class:`~app.adapters.mysql.adapter.MySQLAdapter` and
:class:`~app.adapters.mysql.adapter.MariaDBAdapter`. Flavor differences are isolated in
small hook methods that the two concrete classes override; nothing here assumes that
MariaDB behaves like MySQL.
"""

from __future__ import annotations

import re
import socket
import ssl
from typing import Any, ClassVar

from app.adapters import drivers, tls
from app.adapters.base import (
    AdapterAuthenticationError,
    AdapterConnectionError,
    AdapterDatabaseNotFound,
    AdapterError,
    AdapterPermissionError,
    AdapterTimeout,
    AdapterTLSError,
    ServerInfo,
)
from app.adapters.dbapi import AdapterObjectMissing, SyncDBAPIAdapter, clean_message, num, text
from app.adapters.mysql import sql as Q

AUTH_ERRORS = {1045, 1698, 1251, 2059, 1862, 3118}
DB_MISSING = {1049}
PRIV_ERRORS = {1227, 1142, 1143, 1370, 1141, 3523, 1449, 1095, 1044}
MISSING_ERRORS = {1146, 1109, 1054, 1193, 1305, 1356, 1286, 1275, 1235, 1288}
TIMEOUT_ERRORS = {3024, 1969, 1317, 1205, 3572}
CONN_ERRORS = {2002, 2003, 2005, 2006, 2013, 2055, 1040, 1129, 1130, 1152, 1158, 1159, 1160, 1161}


def parse_version(v: str) -> int:
    """'8.0.46-0ubuntu0.24.04.4' → 80046, '10.11.14-MariaDB-…' → 101114."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", v or "")
    if not m:
        return 0
    a, b, c = (int(x) for x in m.groups())
    return a * 10000 + b * 100 + c


def account(user: Any, host: Any) -> str:
    return f"{text(user) or ''}@{text(host) or ''}"


def normalize_grantee(g: Any) -> str:
    """information_schema GRANTEE "'app'@'%'" → app@% (roles without host keep the bare name)."""
    s = text(g) or ""
    m = re.match(r"^'(.*)'@'(.*)'$", s)
    if m:
        return f"{m.group(1)}@{m.group(2)}"
    return s.strip("'`")


def qident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


class MySQLFamilyBase(SyncDBAPIAdapter):
    driver_key = "pymysql"
    driver = "PyMySQL"
    paramstyle = "%s"
    always_bind = True
    default_port = 3306
    #: True for the MariaDB adapter (flavor hooks)
    flavor: ClassVar[str] = "mysql"
    connection_options: ClassVar[list[dict[str, Any]]] = [
        {
            "key": "host_memory_gb",
            "label": "Host memory (GB)",
            "type": "number",
            "help": "MySQL/MariaDB do not expose physical RAM through SQL; used by the buffer pool sizing advice.",
        },
    ]

    def __init__(self, spec):  # type: ignore[no-untyped-def]
        super().__init__(spec)
        self._info: dict[str, Any] | None = None

    # ------------------------------------------------------------------------------------
    # flavor hooks
    # ------------------------------------------------------------------------------------
    def _session_setup(self, timeout_s: float) -> list[str]:
        """Statements that bound the runtime of every statement in the session."""
        return []

    def _timeout_statements(self, timeout_s: float) -> list[str]:
        return self._session_setup(timeout_s)

    # ------------------------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------------------------
    def _use_database(self, conn: Any, database: str | None) -> Any:
        """MySQL/MariaDB keep one connection per server: a workspace statement or EXPLAIN for
        another database switches the connection's default database (``COM_INIT_DB``, i.e.
        ``USE``) and switches back afterwards, so unqualified names resolve where the user
        chose — never silently in the registered default database."""
        target = (database or "").strip()
        if not target or target == (self.spec.database or ""):
            return None
        conn.select_db(target)
        default = self.spec.database

        def restore() -> None:
            try:
                if default:
                    conn.select_db(default)
                    return
            except Exception:  # noqa: BLE001, S110 - fall through: drop the connection
                pass
            # no default database to return to (MySQL cannot "unselect"): drop the connection,
            # the next call opens a fresh one without a current database
            self._conns.pop("", None)
            try:
                conn.close()
            except Exception:  # noqa: BLE001, S110
                pass

        return restore

    def _open(self, database: str | None) -> Any:
        pymysql = drivers.load("pymysql")
        mode = (self.spec.tls_mode or "prefer").lower()
        kwargs: dict[str, Any] = {
            "host": self.spec.host,
            "port": int(self.spec.port),
            "user": self.spec.username or "",
            "password": self.spec.password or "",
            "database": database or None,
            "connect_timeout": max(1, int(round(self.spec.connect_timeout))),
            "read_timeout": max(5, int(self.spec.statement_timeout + 5)),
            "write_timeout": max(5, int(self.spec.statement_timeout + 5)),
            "charset": "utf8mb4",
            "autocommit": False,
            "program_name": self.spec.application_name[:32],
        }
        if mode == "disable":
            kwargs["ssl_disabled"] = True
        else:
            ctx = tls.ssl_context(mode, self.spec.tls_ca_pem)
            if ctx is None:  # prefer without a CA: opportunistic TLS, as the mysql client does
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            kwargs["ssl"] = ctx
        conn = pymysql.connect(**kwargs)
        try:
            if tls.requires_tls(mode):
                # PyMySQL silently stays in plain text when the server offers no TLS: check it
                with conn.cursor() as cur:
                    cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                    row = cur.fetchone()
                if not row or not row[1]:
                    raise AdapterTLSError(
                        f"tls_mode={mode} but the server did not negotiate TLS (enable TLS on the server, "
                        "or choose prefer/disable)"
                    )
            with conn.cursor() as cur:
                cur.execute("SET SESSION time_zone = '+00:00'")
                cur.execute("SET SESSION lock_wait_timeout = 10")
                cur.execute("SET SESSION innodb_lock_wait_timeout = 5")
                for stmt in self._session_setup(self.spec.statement_timeout):
                    cur.execute(stmt)
            conn.commit()
        except Exception:
            conn.close()
            raise
        return conn

    def _set_call_timeout(self, conn: Any, seconds: float) -> None:
        conn._read_timeout = max(5, int(seconds + 5))  # PyMySQL socket timeout for the next reads
        with conn.cursor() as cur:
            for stmt in self._timeout_statements(seconds):
                cur.execute(stmt)

    def _begin_read_only(self, cursor: Any) -> None:
        cursor.execute("START TRANSACTION READ ONLY")

    def _type_name(self, type_code: Any) -> str:
        try:
            from pymysql.constants import FIELD_TYPE  # noqa: PLC0415

            for k, v in vars(FIELD_TYPE).items():
                if v == type_code and not k.startswith("_"):
                    return k.lower()
        except Exception:  # noqa: BLE001, S110
            pass
        return str(type_code)

    def _classify(self, exc: BaseException) -> AdapterError:
        msg = clean_message(str(exc))
        code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        detail = exc.args[1] if len(exc.args) > 1 else msg
        detail = clean_message(str(detail))
        if (
            isinstance(exc, ssl.SSLError)
            or code == 2026
            or (code in (2003, 2013, None) and ("SSL" in msg or "TLS" in msg))
        ):
            return AdapterTLSError(f"TLS negotiation failed: {detail}")
        if code in AUTH_ERRORS:
            return AdapterAuthenticationError(f"authentication failed: {detail}")
        if code in DB_MISSING:
            return AdapterDatabaseNotFound(f"database not found: {detail}")
        if code in PRIV_ERRORS:
            return AdapterPermissionError(f"insufficient privileges: {detail}")
        if code in MISSING_ERRORS:
            return AdapterObjectMissing(detail)
        if code in TIMEOUT_ERRORS or "timed out" in msg.lower():
            return AdapterTimeout(f"statement timed out: {detail}")
        if code in CONN_ERRORS or isinstance(exc, (socket.timeout, ConnectionError, OSError)):
            return AdapterConnectionError(f"cannot connect to {self.spec.host}:{self.spec.port}: {detail}")
        return AdapterError(detail)

    # ------------------------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------------------------
    async def _status(self) -> dict[str, str]:
        rows = await self._query(Q.STATUS)
        return {str(r["variable_name"]).lower(): r["value"] for r in rows}

    async def _variables(self, names: list[str]) -> dict[str, Any]:
        rows = await self._query(Q.VARIABLES_IN.format(names=", ".join(["%s"] * len(names))), names)
        return {str(r["variable_name"]).lower(): r["value"] for r in rows}

    async def _server(self) -> dict[str, Any]:
        if self._info is None:
            r = await self._one(Q.SERVER_INFO)
            assert r is not None
            r = dict(r)
            r["version_num"] = parse_version(text(r["version"]) or "")
            r["is_mariadb"] = "mariadb" in (text(r["version"]) or "").lower()
            r.update(await self._flavor_info())
            self._info = r
        return self._info

    async def _flavor_info(self) -> dict[str, Any]:
        return {}

    async def _version(self) -> int:
        return int((await self._server())["version_num"])

    async def _cloud_provider(self) -> str | None:
        basedir = await self._try_one("SELECT @@basedir AS basedir")
        if basedir and "rdsdbbin" in (text(basedir["basedir"]) or ""):
            aurora = await self._try_one("SELECT @@aurora_version AS v")
            return "aws-aurora" if aurora else "aws-rds"
        info = await self._server()
        comment = (text(info.get("version_comment")) or "").lower()
        if "google" in comment:
            return "gcp-cloudsql"
        if (text(info.get("hostname")) or "").endswith(".mysql.database.azure.com"):
            return "azure"
        return None

    async def get_server_info(self) -> ServerInfo:
        r = await self._server()
        up = await self._one("SHOW GLOBAL STATUS LIKE 'Uptime'")
        uptime = num(up["value"]) if up else None
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        started = datetime.now(UTC) - timedelta(seconds=uptime) if uptime else None
        repl = await self._replica_rows()
        return ServerInfo(
            engine=self.engine,
            version=(text(r["version"]) or "").split("-")[0],
            version_num=r["version_num"],
            edition=text(r.get("version_comment")),
            started_at=started,
            uptime_seconds=uptime,
            is_replica=bool(repl) if repl is not None else None,
            cloud_provider=await self._cloud_provider(),
            current_user=text(r["current_account"]),
            hostname=text(r["hostname"]),
            extra={
                "version_full": text(r["version"]),
                "flavor_detected": "mariadb" if r["is_mariadb"] else "mysql",
                "server_id": r.get("server_id"),
                "server_uuid": r.get("server_uuid"),
                "read_only": bool(int(r.get("read_only") or 0)),
                "super_read_only": r.get("super_read_only"),
                "character_set": text(r.get("charset")),
                "collation": text(r.get("collation_name")),
                "performance_schema": bool(int(r.get("performance_schema") or 0)),
                "platform": f"{text(r.get('os'))} / {text(r.get('machine'))}",
                "port": r.get("port"),
            },
        )

    async def _replica_rows(self) -> list[dict[str, Any]] | None:
        """Replica status rows; [] = not a replica; None = cannot tell (privilege)."""
        v = await self._version()
        sql = "SHOW REPLICA STATUS" if self._has_show_replica(v) else "SHOW SLAVE STATUS"
        try:
            return await self._query(sql)
        except AdapterPermissionError:
            return None
        except AdapterError:
            return None

    def _has_show_replica(self, version_num: int) -> bool:
        return version_num >= 80022
