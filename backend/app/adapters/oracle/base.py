"""Oracle connection handling (python-oracledb *thin* mode), error mapping and discovery.

Thin mode talks Oracle Net directly: no Oracle Client / Instant Client is installed or
redistributed with the platform. Consequences, stated honestly in capabilities and docs:

* Native Network Encryption (``SQLNET.ENCRYPTION_SERVER``) is not available in thin mode; use
  TCPS (TLS). A server that *requires* NNE rejects the connection (DPY-3001).
* Password verifiers must be 11G or newer (10G-only accounts fail with DPY-3015).
"""

from __future__ import annotations

import re
import ssl
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
from app.adapters.dbapi import AdapterObjectMissing, SyncDBAPIAdapter, clean_message, num, text, utc
from app.adapters.oracle import sql as Q

#: Oracle error numbers → normalized classes
AUTH_CODES = {1017, 28000, 28001, 28009, 28040, 1005, 1004, 28030, 28273}
PRIV_CODES = {1031, 942, 1039, 1924, 4043, 1045, 31603, 1456}  # 942/4043: not visible; 1456: write in READ ONLY txn
NOT_FOUND_CODES = {12514, 12505, 65011, 1034}
CONN_CODES = {
    12541,
    12543,
    12545,
    12170,
    12535,
    12537,
    12547,
    3113,
    3114,
    3135,
    12152,
    1033,
    1089,
    12528,
    12516,
    12519,
    12520,
}
TIMEOUT_CODES = {1013, 54, 30006, 4021, 3136}
MISSING_CODES = {904}  # invalid identifier: column absent on this version

#: Premier/Extended Support end for releases that are already past it (newer releases have
#: Oracle-published dates that change; they are not hard-coded here)
ENDED_SUPPORT = {11: "2020-12-31", 12: "2022-07-31", 18: "2021-06-30"}
#: releases with Oracle-published long-term support (informational)
RELEASES = {
    12: "Oracle Database 12c",
    18: "Oracle Database 18c",
    19: "Oracle Database 19c",
    21: "Oracle Database 21c",
    23: "Oracle Database 23ai",
}


def qid(name: str) -> str:
    """Quoted Oracle identifier. Names reach here validated (A–Z, 0–9, _ $ #) and upper-cased."""
    return '"' + name.replace('"', '""') + '"'


def parse_version(v: str | None) -> tuple[int, int, int]:
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", v or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


class OracleBase(SyncDBAPIAdapter):
    engine = "oracle"
    display_name = "Oracle Database"
    default_port = 1521
    driver = "python-oracledb (thin mode)"
    driver_key = "oracledb"
    paramstyle = ":name"
    connection_per_database = False
    connection_options: ClassVar[list[dict[str, Any]]] = [
        {
            "key": "service_name",
            "label": "Service name",
            "type": "string",
            "help": "Oracle Net service (a PDB service such as ORCLPDB1, or the CDB service). Preferred over SID.",
        },
        {
            "key": "sid",
            "label": "SID",
            "type": "string",
            "help": "Legacy instance SID; leave empty when a service name is given.",
        },
        {
            "key": "protocol",
            "label": "Protocol",
            "type": "choice",
            "choices": ["tcp", "tcps"],
            "help": "tcps = TLS. Defaults to tcps when the TLS mode is require or verify-*.",
        },
        {
            "key": "ssl_server_cert_dn",
            "label": "Server certificate DN",
            "type": "string",
            "help": "Expected distinguished name of the server certificate (verify-full).",
        },
        {
            "key": "expire_time",
            "label": "Keep-alive (minutes)",
            "type": "integer",
            "help": "Dead-connection detection probe interval (EXPIRE_TIME).",
        },
    ]

    def __init__(self, spec):  # type: ignore[no-untyped-def]
        super().__init__(spec)
        self._info: dict[str, Any] | None = None

    # ---- licensing -------------------------------------------------------------------------
    @property
    def diagnostics_pack(self) -> bool:
        """Licensing flag set by an administrator on the server (default off). The adapter
        does not read pack-licensed views even when it is on; it is recorded and shown."""
        return "diagnostics_pack" in (self.spec.licensed_features or [])

    @property
    def tuning_pack(self) -> bool:
        return "tuning_pack" in (self.spec.licensed_features or [])

    # ---- connection ------------------------------------------------------------------------
    def connect_params(self) -> dict[str, Any]:
        """Keyword arguments for ``oracledb.connect`` without the credentials."""
        o = self.spec.options or {}
        mode = (self.spec.tls_mode or "prefer").lower()
        protocol = str(o.get("protocol") or ("tcps" if tls.requires_tls(mode) else "tcp")).lower()
        params: dict[str, Any] = {
            "host": self.spec.host,
            "port": self.spec.port,
            "protocol": protocol,
            "tcp_connect_timeout": max(1.0, float(self.spec.connect_timeout)),
            "retry_count": 0,
            "program": self.spec.application_name[:48],
            "driver_name": "dbmanager : python-oracledb thin",
        }
        service = o.get("service_name") or (self.spec.database if not o.get("sid") else None)
        if service:
            params["service_name"] = str(service)
        elif o.get("sid"):
            params["sid"] = str(o["sid"])
        if o.get("expire_time"):
            params["expire_time"] = int(o["expire_time"])
        if protocol == "tcps":
            ctx = tls.ssl_context(mode, self.spec.tls_ca_pem)
            if ctx is None:  # prefer/require without verification: encrypt but do not validate
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            params["ssl_context"] = ctx
            params["ssl_server_dn_match"] = mode == "verify-full"
            if o.get("ssl_server_cert_dn"):
                params["ssl_server_cert_dn"] = str(o["ssl_server_cert_dn"])
        return params

    def _open(self, database: str | None) -> Any:
        oracledb = drivers.load("oracledb")
        conn = oracledb.connect(user=self.spec.username, password=self.spec.password, **self.connect_params())
        conn.call_timeout = int(max(1, self.spec.statement_timeout) * 1000)

        def lobs_as_values(cursor: Any, metadata: Any) -> Any:
            if metadata.type_code is oracledb.DB_TYPE_CLOB or metadata.type_code is oracledb.DB_TYPE_NCLOB:
                return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)
            if metadata.type_code is oracledb.DB_TYPE_BLOB:
                return cursor.var(oracledb.DB_TYPE_LONG_RAW, arraysize=cursor.arraysize)
            return None

        conn.outputtypehandler = lobs_as_values
        cur = conn.cursor()
        try:
            cur.execute("ALTER SESSION SET TIME_ZONE = '+00:00'")
            cur.execute("ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS'")
            cur.execute("ALTER SESSION SET DDL_LOCK_TIMEOUT = 5")
        finally:
            cur.close()
        return conn

    def _set_call_timeout(self, conn: Any, seconds: float) -> None:
        conn.call_timeout = int(max(1, seconds) * 1000)

    def _begin_read_only(self, cursor: Any) -> None:
        cursor.execute("SET TRANSACTION READ ONLY")

    def _type_name(self, type_code: Any) -> str:
        return getattr(type_code, "name", None) or str(type_code)

    def _classify(self, exc: BaseException) -> AdapterError:
        if isinstance(exc, AdapterError):
            return exc
        err = exc.args[0] if exc.args else None
        full = str(getattr(err, "full_code", "") or "")
        code = getattr(err, "code", None)
        code = int(code) if isinstance(code, int) else None
        msg = clean_message(str(getattr(err, "message", None) or exc))
        low = msg.lower()
        if full == "DPY-3001":
            return AdapterConnectionError(
                f"the server requires Native Network Encryption, which the thin driver does not support; "
                f"configure a TCPS listener and use protocol tcps ({msg})"
            )
        if full == "DPY-3015":
            return AdapterAuthenticationError(
                "the account only has a 10G password verifier; reset its password with SQLNET.ALLOWED_LOGON_VERSION "
                f"of 11 or later ({msg})"
            )
        if "ssl" in low or "certificate" in low or "tls" in low or "handshake" in low:
            return AdapterTLSError(f"TLS negotiation failed: {msg}")
        if code in AUTH_CODES:
            return AdapterAuthenticationError(f"authentication failed: {msg}")
        # thin mode wraps listener refusals: DPY-6005 "cannot connect" + DPY-6001 "Service … is not
        # registered with the listener" (ORA-12514) / DPY-6003 "SID … not registered" (ORA-12505)
        if code in NOT_FOUND_CODES or any(m in msg for m in ("DPY-6001", "DPY-6003", "ORA-12514", "ORA-12505")):
            return AdapterDatabaseNotFound(f"service/database not available: {msg}")
        if full == "DPY-4024" or code in TIMEOUT_CODES:
            return AdapterTimeout(f"timed out: {msg}")
        if code in CONN_CODES or full.startswith("DPY-6") or full == "DPY-4011":
            return AdapterConnectionError(f"cannot connect to {self.spec.host}:{self.spec.port}: {msg}")
        if code in PRIV_CODES:
            return AdapterPermissionError(f"insufficient privileges (or object not visible to this account): {msg}")
        if code in MISSING_CODES:
            return AdapterObjectMissing(msg)
        return AdapterError(msg)

    # ---- discovery -------------------------------------------------------------------------
    async def _server(self) -> dict[str, Any]:
        if self._info is None:
            r = await self._try_one(Q.SERVER_INFO)
            full_access = r is not None
            if r is None:
                r = await self._one(Q.BASIC_INFO)
                assert r is not None
            r = dict(r)
            r["v_views"] = full_access
            vf = await self._try_one(Q.VERSION_FULL) if full_access else None
            r["version_full"] = text(vf["version_full"]) if vf else None
            ver = r["version_full"] or text(r.get("version")) or ""
            major, minor, ru = parse_version(ver)
            r["major"], r["version_num"] = major, major * 10_000 + minor * 100 + ru
            banner = await self._try_one(Q.BANNER) if full_access else None
            r["banner"] = text(banner["banner"]) if banner else None
            r["is_cdb"] = text(r.get("cdb")) == "YES"
            r["con_name"] = text(r.get("con_name"))
            r["con_id"] = int(num(r.get("con_id"), 0) or 0)
            r["in_root"] = r["is_cdb"] and r["con_name"] == "CDB$ROOT"
            privs = await self._try(Q.SESSION_PRIVS) or []
            roles = await self._try(Q.SESSION_ROLES) or []
            r["session_privs"] = sorted(text(p["privilege"]) or "" for p in privs)
            r["session_roles"] = sorted(text(p["role"]) or "" for p in roles)
            os_rows = await self._try(Q.CPU_COUNT) if full_access else None
            r["osstat"] = {text(x["stat_name"]): num(x["value"]) for x in os_rows or []}
            self._info = r
        return self._info

    async def _version(self) -> int:
        return int((await self._server())["major"])

    def _edition(self, banner: str | None) -> str | None:
        if not banner:
            return None
        for e in (
            "Enterprise Edition",
            "Standard Edition 2",
            "Standard Edition",
            "Express Edition",
            "Free",
            "Personal Edition",
        ):
            if e in banner:
                return e
        return None

    async def get_server_info(self) -> ServerInfo:
        r = await self._server()
        started = utc(r.get("startup_time"))
        major = int(r["major"])
        edition = self._edition(r.get("banner"))
        role = text(r.get("database_role"))
        privs = set(r["session_privs"])
        return ServerInfo(
            engine=self.engine,
            version=r.get("version_full") or text(r.get("version")) or "",
            version_num=r["version_num"],
            edition=" ".join(x for x in (RELEASES.get(major, f"Oracle Database {major}"), edition) if x),
            started_at=started,
            uptime_seconds=(datetime.now(UTC) - started).total_seconds() if started else None,
            is_replica=role is not None and role != "PRIMARY",
            cloud_provider=None,
            current_user=text(r.get("session_user")),
            hostname=text(r.get("host_name")),
            extra={
                "banner": r.get("banner"),
                "edition": edition,
                "instance_name": text(r.get("instance_name")),
                "db_name": text(r.get("db_name")),
                "db_unique_name": text(r.get("db_unique_name")),
                "database_role": role,
                "open_mode": text(r.get("open_mode")),
                "log_mode": text(r.get("log_mode")),
                "force_logging": text(r.get("force_logging")),
                "flashback_on": text(r.get("flashback_on")),
                "platform": text(r.get("platform_name")),
                "rac": text(r.get("parallel")) == "YES",
                "cdb": r["is_cdb"],
                "container": r["con_name"],
                "con_id": r["con_id"],
                "in_root": r["in_root"],
                "service_name": text(r.get("service_name")),
                "network_protocol": text(r.get("network_protocol")),
                "v_views_readable": r["v_views"],
                "select_any_dictionary": "SELECT ANY DICTIONARY" in privs,
                "select_catalog_role": "SELECT_CATALOG_ROLE" in set(r["session_roles"]),
                "alter_system": "ALTER SYSTEM" in privs,
                "cpu_count": int(r["osstat"].get("NUM_CPUS") or 0) or None,
                "physical_memory_bytes": r["osstat"].get("PHYSICAL_MEMORY_BYTES"),
                "diagnostics_pack_licensed": self.diagnostics_pack,
                "tuning_pack_licensed": self.tuning_pack,
                "end_of_support": ENDED_SUPPORT.get(major),
            },
        )


__all__ = ["OracleBase", "qid", "parse_version"]
