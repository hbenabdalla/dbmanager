"""Db2 connection handling (ibm_db / ibm_db_dbi with the CLI driver bundled in the wheel),
SQLCODE-based error mapping and server discovery. Shared by the Db2 LUW and Db2 for z/OS
adapters; the z/OS adapter overrides discovery and every catalog query."""

from __future__ import annotations

import re
from datetime import UTC, datetime
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
from app.adapters.db2 import sql as Q
from app.adapters.dbapi import AdapterObjectMissing, SyncDBAPIAdapter, clean_message, num, text, utc

AUTH_CODES = {-30082, -1060, -1639}
NOT_FOUND_CODES = {-1013, -30061, -1031}
CONN_CODES = {-30081, -1336, -30080, -1224, -1032, -30108, -4499, -1776}
PRIV_CODES = {-551, -552, -1092, -727, -5001, -556, -20140}
MISSING_CODES = {-204, -206, -440, -443, -4302, -805}
TIMEOUT_CODES = {-911, -913, -952}
LICENSE_CODES = {-8002, -1598}
#: Db2 LUW releases whose support has ended (newer releases have IBM-published dates)
ENDED_SUPPORT = {9: "2017-09-30", 10: "2020-04-30"}
_SQLCODE = re.compile(r"SQLCODE=(-?\d+)")
_SQLN = re.compile(r"SQL(\d{4,5})N")
_SQLSTATE = re.compile(r"SQLSTATE=(\w{5})")


def qid(name: str) -> str:
    """Delimited Db2 identifier (names are validated and upper-cased before reaching here)."""
    return '"' + name.replace('"', '""') + '"'


def _cli_value(v: str) -> str:
    """CLI connection-string value: brace-quoted when it contains a delimiter."""
    if any(ch in v for ch in ";{}="):
        return "{" + v.replace("}", "}}") + "}"
    return v


def parse_service_level(level: str | None) -> tuple[int, int, int, int]:
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)\.(\d+)", level or "")
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0, 0)  # type: ignore[return-value]


class Db2Base(SyncDBAPIAdapter):
    engine = "db2"
    display_name = "IBM Db2 LUW"
    default_port = 50000
    driver = "ibm_db (IBM Data Server Driver / CLI, bundled)"
    driver_key = "ibm_db"
    paramstyle = "?"
    connection_options: ClassVar[list[dict[str, Any]]] = [
        {
            "key": "current_schema",
            "label": "Current schema",
            "type": "string",
            "help": "Default schema for unqualified names in the SQL workspace.",
        },
        {
            "key": "ssl_server_certificate",
            "label": "Server certificate file",
            "type": "string",
            "help": "Not needed when a CA certificate is uploaded with the server (TLS settings).",
        },
        {
            "key": "authentication",
            "label": "Client authentication",
            "type": "choice",
            "choices": ["SERVER", "SERVER_ENCRYPT", "SERVER_ENCRYPT_AES"],
            "help": "CLI Authentication keyword; leave empty to follow the server.",
        },
    ]

    def __init__(self, spec):  # type: ignore[no-untyped-def]
        super().__init__(spec)
        self._info: dict[str, Any] | None = None
        self._ca: tls.CAFile | None = None

    # ---- connection -------------------------------------------------------------------------
    def connection_string(self) -> str:
        """CLI connection string without credentials."""
        o = self.spec.options or {}
        if not self.spec.database:
            raise AdapterDatabaseNotFound("a Db2 database name (or z/OS location) is required")
        for v in (self.spec.host, self.spec.database):
            if any(ch in v for ch in ";{}="):
                raise AdapterConnectionError("host and database name cannot contain ; { } =")
        parts = [
            f"DATABASE={self.spec.database}",
            f"HOSTNAME={self.spec.host}",
            f"PORT={int(self.spec.port)}",
            "PROTOCOL=TCPIP",
            f"ConnectTimeout={max(1, int(round(self.spec.connect_timeout)))}",
        ]
        mode = (self.spec.tls_mode or "prefer").lower()
        if tls.requires_tls(mode) or o.get("ssl_server_certificate"):
            parts.append("SECURITY=SSL")
            if self.spec.tls_ca_pem:
                if self._ca is None:
                    self._ca = tls.CAFile(self.spec.tls_ca_pem)
                parts.append(f"SSLServerCertificate={_cli_value(self._ca.path or '')}")
            elif o.get("ssl_server_certificate"):
                parts.append(f"SSLServerCertificate={_cli_value(str(o['ssl_server_certificate']))}")
            if mode == "verify-full":
                # the certificate must also name the host (Db2 CLI 11.5.6+); verify-ca checks the chain only
                parts.append("SSLClientHostnameValidation=Basic")
        if o.get("authentication"):
            parts.append(f"AUTHENTICATION={o['authentication']}")
        if o.get("current_schema"):
            parts.append(f"CURRENTSCHEMA={_cli_value(str(o['current_schema']))}")
        return ";".join(p for p in parts if p) + ";"

    def _open(self, database: str | None) -> Any:
        dbi = drivers.load("ibm_db")
        import ibm_db  # noqa: PLC0415 - loaded together with ibm_db_dbi

        creds = f"UID={_cli_value(self.spec.username or '')};PWD={_cli_value(self.spec.password or '')};"
        conn = dbi.connect(self.connection_string() + creds, "", "")
        try:
            conn.set_option({ibm_db.SQL_ATTR_QUERY_TIMEOUT: max(1, int(self.spec.statement_timeout))})
        except Exception:  # noqa: BLE001, S110 - not every CLI level accepts it on the connection
            pass
        cur = conn.cursor()
        try:
            cur.execute("SET CURRENT LOCK TIMEOUT = 5")
        except Exception:  # noqa: BLE001, S110 - z/OS uses IRLM timeouts (no special register)
            pass
        finally:
            cur.close()
        return conn

    def _query_sync(self, sql: str, params: Any, database: str | None, max_rows: int | None) -> list[dict[str, Any]]:
        """Monitoring reads end their unit of work at once (the driver runs with autocommit off),
        so the monitoring connection never shows up as an open transaction holding locks."""
        conn = self._get_conn(database)
        try:
            return super()._query_sync(sql, params, database, max_rows)
        finally:
            try:
                conn.commit()
            except Exception:  # noqa: BLE001, S110 - the connection error is reported by the query
                pass

    async def close(self) -> None:
        await super().close()
        if self._ca is not None:
            self._ca.close()
            self._ca = None

    def _set_call_timeout(self, conn: Any, seconds: float) -> None:
        try:
            import ibm_db  # noqa: PLC0415

            conn.set_option({ibm_db.SQL_ATTR_QUERY_TIMEOUT: max(1, int(seconds))})
        except Exception:  # noqa: BLE001, S110
            pass

    def _classify(self, exc: BaseException) -> AdapterError:
        if isinstance(exc, AdapterError):
            return exc
        msg = clean_message(str(exc))
        low = msg.lower()
        m = _SQLCODE.search(msg)
        code = int(m.group(1)) if m else None
        if code is None:
            n = _SQLN.search(msg)
            code = -int(n.group(1)) if n else None
        state = (_SQLSTATE.search(msg) or [None, None])[1]  # type: ignore[index]
        if "gsk" in low or "ssl" in low or "tls" in low or "certificate" in low:
            return AdapterTLSError(f"TLS negotiation failed: {msg}")
        if code in LICENSE_CODES:
            return AdapterConnectionError(
                f"license check failed — DRDA connections to Db2 for z/OS or IBM i need a Db2 Connect license "
                f"(or server-side activation): {msg}"
            )
        if code in AUTH_CODES or state in ("28000",):
            return AdapterAuthenticationError(f"authentication failed: {msg}")
        if code in NOT_FOUND_CODES or (state == "08004" and "not found" in low):
            return AdapterDatabaseNotFound(f"database not found: {msg}")
        if code in CONN_CODES or state in ("08001", "08003", "08S01", "40003"):
            return AdapterConnectionError(f"cannot connect to {self.spec.host}:{self.spec.port}: {msg}")
        if code in PRIV_CODES or state in ("42501", "42502", "42512"):
            return AdapterPermissionError(f"insufficient privileges: {msg}")
        if code in MISSING_CODES or state in ("42704", "42703", "42884", "51002"):
            return AdapterObjectMissing(msg)
        if code in TIMEOUT_CODES or state in ("40001", "57033", "57014", "HYT00"):
            return AdapterTimeout(f"timed out (lock timeout, deadlock or interrupted): {msg}")
        return AdapterError(msg)

    # ---- discovery (LUW) ----------------------------------------------------------------------
    async def _server(self) -> dict[str, Any]:
        if self._info is None:
            s = await self._one(Q.SESSION_INFO)
            r: dict[str, Any] = dict(s or {})
            inst = await self._try_one(Q.INST_INFO)
            r["inst"] = dict(inst) if inst else {}
            level = text(r["inst"].get("service_level")) or ""
            v = parse_service_level(level)
            r["major"], r["minor"] = v[0], v[1]
            r["version"] = ".".join(str(x) for x in v) if v[0] else level
            r["version_num"] = v[0] * 1_000_000 + v[1] * 10_000 + v[2] * 100 + v[3]
            prods = await self._try(Q.PROD_INFO) or []
            lic = [p for p in prods if text(p.get("license_installed")) == "Y"]
            r["product"] = text((lic or prods or [{}])[0].get("installed_prod_fullname"))
            sysinfo = await self._try_one(Q.SYS_INFO)
            r["sys"] = dict(sysinfo) if sysinfo else {}
            auth = await self._try(Q.MY_AUTHORITIES) or []
            r["authorities"] = sorted(
                text(a["authority"]) or ""
                for a in auth
                if any(
                    text(a.get(k)) == "Y"
                    for k in ("d_user", "d_group", "d_public", "role_user", "role_group", "role_public", "d_role")
                )
            )
            r["mon_routines"] = await self._try_one(Q.CONNECTION_COUNT) is not None
            self._info = r
        return self._info

    async def _version(self) -> int:
        return int((await self._server())["major"])

    async def get_server_info(self) -> ServerInfo:
        r = await self._server()
        started = None
        row = await self._try_one(Q.DB_START)
        if row and row.get("started") is not None:
            started = utc(row["started"])
        inst_start = await self._try_one(Q.INSTANCE_START)
        sys = r["sys"]
        return ServerInfo(
            engine=self.engine,
            version=r["version"],
            version_num=r["version_num"],
            edition=r.get("product") or text(r["inst"].get("service_level")),
            started_at=started,
            uptime_seconds=(datetime.now(UTC) - started).total_seconds() if started else None,
            is_replica=None,
            current_user=text(r.get("cur_user")),
            hostname=text(sys.get("host_name")),
            extra={
                "database": text(r.get("db_name")),
                "instance": text(r["inst"].get("inst_name")),
                "service_level": text(r["inst"].get("service_level")),
                "build_level": text(r["inst"].get("bld_level")),
                "fixpack": r["inst"].get("fixpack_num"),
                "members": r["inst"].get("num_members"),
                "os": " ".join(
                    x for x in (text(sys.get("os_name")), text(sys.get("os_version")), text(sys.get("os_release"))) if x
                ),
                "cpu_count": sys.get("total_cpus"),
                "memory_mb": sys.get("total_memory"),
                "instance_started": jsonable_ts(inst_start.get("started")) if inst_start else None,
                "session_user": text(r.get("sess_user")),
                "current_schema": text(r.get("cur_schema")),
                "authorities": r["authorities"],
                "monitor_routines_executable": r["mon_routines"],
                "end_of_support": ENDED_SUPPORT.get(int(r["major"] or 0)),
            },
        )


def jsonable_ts(v: Any) -> str | None:
    d = utc(v)
    return d.isoformat() if d else None


__all__ = ["Db2Base", "num", "parse_service_level", "qid"]
