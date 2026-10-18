"""SQL Server connection management and discovery (pyodbc + Microsoft ODBC Driver 18).

Authentication: SQL Server logins are supported. Microsoft Entra ID password and service
principal authentication are passed through to the ODBC driver (``authentication`` option)
for Azure SQL; they are implemented but not validated against Azure in this project.
Windows/Active Directory *integrated* (Kerberos) authentication is not implemented: it needs
a Kerberos client configuration and keytab inside the backend container (see
docs/database-engines/sql-server.md).
"""

from __future__ import annotations

import re
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
    DriverNotInstalled,
    ServerInfo,
)
from app.adapters.dbapi import AdapterObjectMissing, SyncDBAPIAdapter, clean_message, num, text, utc
from app.adapters.mssql import sql as Q

ENGINE_EDITIONS = {
    1: "Personal/Desktop",
    2: "Standard",
    3: "Enterprise",
    4: "Express",
    5: "Azure SQL Database",
    6: "Azure Synapse Analytics",
    8: "Azure SQL Managed Instance",
    9: "Azure SQL Edge",
    11: "Azure Synapse serverless",
}
AUTH_MODES = {
    "sql": None,
    "entra_password": "ActiveDirectoryPassword",
    "entra_service_principal": "ActiveDirectoryServicePrincipal",
}
#: SQL Server versions: major → (name, end of extended support)
VERSIONS = {
    11: ("SQL Server 2012", "2022-07-12"),
    12: ("SQL Server 2014", "2024-07-09"),
    13: ("SQL Server 2016", "2026-07-14"),
    14: ("SQL Server 2017", "2027-10-12"),
    15: ("SQL Server 2019", "2030-01-08"),
    16: ("SQL Server 2022", "2033-01-11"),
    17: ("SQL Server 2025", None),
}


def qn(name: str) -> str:
    """T-SQL bracket-quoted identifier."""
    return "[" + name.replace("]", "]]") + "]"


def _odbc_value(v: str) -> str:
    """Brace-quote an ODBC connection string value (``}`` doubled)."""
    return "{" + v.replace("}", "}}") + "}"


class SQLServerBase(SyncDBAPIAdapter):
    engine = "mssql"
    display_name = "Microsoft SQL Server"
    default_port = 1433
    driver = "pyodbc + Microsoft ODBC Driver 18"
    driver_key = "pyodbc"
    paramstyle = "?"
    connection_per_database = True
    connection_options: ClassVar[list[dict[str, Any]]] = [
        {
            "key": "instance_name",
            "label": "Instance name",
            "type": "string",
            "help": "Named instance (host\\instance); the port is then resolved by SQL Browser unless set.",
        },
        {
            "key": "encrypt",
            "label": "Encryption",
            "type": "choice",
            "choices": ["optional", "mandatory", "strict"],
            "help": "Overrides the TLS mode mapping; strict = TDS 8.0 (SQL Server 2022+ / Azure SQL).",
        },
        {
            "key": "trust_server_certificate",
            "label": "Trust server certificate",
            "type": "boolean",
            "help": "Skip certificate validation (test systems only).",
        },
        {
            "key": "host_name_in_certificate",
            "label": "Host name in certificate",
            "type": "string",
            "help": "Expected certificate host name when connecting through an alias or IP.",
        },
        {
            "key": "application_intent",
            "label": "Application intent",
            "type": "choice",
            "choices": ["ReadWrite", "ReadOnly"],
            "help": "ReadOnly routes to a readable secondary of an availability group.",
        },
        {
            "key": "multi_subnet_failover",
            "label": "Multi-subnet failover",
            "type": "boolean",
            "help": "For availability group listeners spanning subnets.",
        },
        {
            "key": "authentication",
            "label": "Authentication",
            "type": "choice",
            "choices": list(AUTH_MODES),
            "help": "sql = SQL Server login; entra_* = Microsoft Entra ID (Azure SQL).",
        },
        {
            "key": "odbc_driver",
            "label": "ODBC driver name",
            "type": "string",
            "help": "Defaults to the newest installed Microsoft ODBC Driver for SQL Server.",
        },
    ]

    def __init__(self, spec):  # type: ignore[no-untyped-def]
        super().__init__(spec)
        self._info: dict[str, Any] | None = None
        self._ca: tls.CAFile | None = None

    # ------------------------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------------------------
    def connection_string(self, database: str | None) -> tuple[str, list[str]]:
        """ODBC connection string and the human-readable notes about its TLS settings.
        Credentials are appended separately and never logged."""
        o = self.spec.options or {}
        driver = drivers.odbc_driver_name(o.get("odbc_driver"))
        server = self.spec.host
        if o.get("instance_name"):
            server = f"{server}\\{o['instance_name']}"
            if o.get("port_with_instance"):
                server += f",{self.spec.port}"
        else:
            server = f"{server},{self.spec.port}"
        mode = (self.spec.tls_mode or "prefer").lower()
        encrypt = {"disable": "no", "prefer": "optional", "require": "mandatory"}.get(mode, "mandatory")
        trust = mode in ("prefer", "require")
        notes = []
        if o.get("encrypt"):
            encrypt = str(o["encrypt"])
        if "trust_server_certificate" in o:
            trust = bool(o["trust_server_certificate"])
        parts = {
            "DRIVER": _odbc_value(driver),
            "SERVER": _odbc_value(server),
            "Encrypt": "Optional"
            if encrypt == "optional"
            else ("Strict" if encrypt == "strict" else ("no" if encrypt == "no" else "yes")),
            "TrustServerCertificate": "yes" if trust and encrypt != "strict" else "no",
            "LoginTimeout": str(max(1, int(round(self.spec.connect_timeout)))),
            "APP": _odbc_value(self.spec.application_name[:128]),
        }
        if database:
            parts["DATABASE"] = _odbc_value(database)
        if o.get("host_name_in_certificate"):
            parts["HostNameInCertificate"] = _odbc_value(str(o["host_name_in_certificate"]))
        if self.spec.tls_ca_pem and tls.verifies_certificate(mode):
            # ODBC Driver 18.1+ pins the server certificate (or its issuing CA bundle on Linux
            # through the system trust store); the PEM is written to a private temp file.
            if self._ca is None:
                self._ca = tls.CAFile(self.spec.tls_ca_pem)
            parts["ServerCertificate"] = _odbc_value(self._ca.path or "")
            notes.append("server certificate pinned with ServerCertificate (ODBC Driver 18.1+)")
        if o.get("application_intent"):
            parts["ApplicationIntent"] = str(o["application_intent"])
        if o.get("multi_subnet_failover"):
            parts["MultiSubnetFailover"] = "yes"
        auth = AUTH_MODES.get(str(o.get("authentication") or "sql"))
        if auth:
            parts["Authentication"] = auth
        return ";".join(f"{k}={v}" for k, v in parts.items()), notes

    def _open(self, database: str | None) -> Any:
        pyodbc = drivers.load("pyodbc")
        conn_str, _ = self.connection_string(database or self.spec.database)
        creds = f";UID={_odbc_value(self.spec.username or '')};PWD={_odbc_value(self.spec.password or '')}"
        conn = pyodbc.connect(conn_str + creds, autocommit=True, timeout=max(1, int(round(self.spec.connect_timeout))))
        conn.timeout = max(1, int(self.spec.statement_timeout))  # query timeout (seconds)
        cur = conn.cursor()
        try:
            cur.execute("SET LOCK_TIMEOUT 5000; SET DEADLOCK_PRIORITY LOW; SET ARITHABORT ON")
        finally:
            cur.close()
        return conn

    async def close(self) -> None:
        await super().close()
        if self._ca is not None:
            self._ca.close()
            self._ca = None

    def _set_call_timeout(self, conn: Any, seconds: float) -> None:
        conn.timeout = max(1, int(seconds))

    def _type_name(self, type_code: Any) -> str:
        return getattr(type_code, "__name__", str(type_code))

    def _classify(self, exc: BaseException) -> AdapterError:
        if isinstance(exc, AdapterError):
            return exc
        state = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
        msg = clean_message(" ".join(str(a) for a in exc.args[1:]) if len(exc.args) > 1 else str(exc))
        low = msg.lower()
        native = re.search(r"\((\d+)\)", msg)
        code = int(native.group(1)) if native else None
        if state in ("IM002", "IM003", "01000") and ("can't open lib" in low or "data source name not found" in low):
            return DriverNotInstalled(f"ODBC driver not available: {msg}")
        if "ssl" in low or "certificate" in low or "tls" in low or "encryption not supported" in low:
            return AdapterTLSError(f"TLS negotiation failed: {msg}")
        if state == "28000" or code == 18456:
            if "cannot open database" in low:
                return AdapterDatabaseNotFound(f"database not found or not accessible: {msg}")
            return AdapterAuthenticationError(f"authentication failed: {msg}")
        if code == 4060 or "cannot open database" in low:
            return AdapterDatabaseNotFound(f"database not found or not accessible: {msg}")
        if state in ("HYT00", "HYT01"):
            return (
                AdapterTimeout(f"timed out: {msg}")
                if self._conns
                else AdapterConnectionError(f"login timed out: {msg}")
            )
        if state in ("08001", "08S01", "08004", "08003") or "communication link failure" in low:
            return AdapterConnectionError(f"cannot connect to {self.spec.host}:{self.spec.port}: {msg}")
        if (
            code in (229, 230, 262, 297, 300, 916, 6102, 15247, 15151)
            or "permission was denied" in low
            or "permission denied" in low
            or "does not have permission" in low
        ):
            return AdapterPermissionError(f"insufficient privileges: {msg}")
        if code in (208, 207, 2812, 4121, 195) or state == "42S02" or "invalid object name" in low:
            return AdapterObjectMissing(msg)
        if code == 1222 or "lock request time out" in low:
            return AdapterTimeout(f"lock wait timed out: {msg}")
        if state == "HY008" or "operation canceled" in low:
            return AdapterTimeout(f"statement cancelled: {msg}")
        return AdapterError(msg)

    # ------------------------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------------------------
    async def _server(self) -> dict[str, Any]:
        if self._info is None:
            r = await self._one(Q.SERVER_INFO)
            assert r is not None
            r = dict(r)
            m = re.match(r"(\d+)\.(\d+)\.(\d+)", text(r["product_version"]) or "")
            major, minor, build = (int(x) for x in m.groups()) if m else (0, 0, 0)
            r["major"], r["version_num"] = major, major * 1_000_000 + minor * 10_000 + build
            r["is_azure_db"] = int(r["engine_edition"] or 0) in (5, 6, 9, 11)
            r["is_managed_instance"] = int(r["engine_edition"] or 0) == 8
            self._info = r
        return self._info

    async def _version(self) -> int:
        return int((await self._server())["major"])

    async def get_server_info(self) -> ServerInfo:
        r = await self._server()
        started, sysinfo = None, None
        if not r["is_azure_db"]:
            sysinfo = await self._try_one(Q.START_TIME)
        started = utc(sysinfo["started"]) if sysinfo else None
        if started is None:
            row = await self._try_one(Q.START_TIME_TEMPDB)
            started = utc(row["started"]) if row else None
        from datetime import UTC, datetime  # noqa: PLC0415

        major = int(r["major"])
        name = VERSIONS.get(major, (f"SQL Server (v{major})", None))[0]
        ee = int(r["engine_edition"] or 0)
        cloud = "azure-sql" if ee == 5 else ("azure-sql-mi" if ee == 8 else None)
        return ServerInfo(
            engine=self.engine,
            version=text(r["product_version"]) or "",
            version_num=r["version_num"],
            edition=f"{name} {text(r['edition'])} ({text(r['product_level'])}{' ' + text(r['update_level']) if r.get('update_level') else ''})",
            started_at=started,
            uptime_seconds=(datetime.now(UTC) - started).total_seconds() if started else None,
            is_replica=None,
            cloud_provider=cloud,
            current_user=text(r["login_name"]),
            hostname=text(r["machine_name"]),
            extra={
                "version_full": (text(r["version_full"]) or "").splitlines()[0],
                "product_name": name,
                "product_level": text(r["product_level"]),
                "update_level": text(r["update_level"]),
                "engine_edition": ENGINE_EDITIONS.get(ee, str(ee)),
                "server_name": text(r["server_name"]),
                "instance_name": text(r["instance_name"]) or "MSSQLSERVER",
                "is_clustered": bool(r["is_clustered"]),
                "is_hadr_enabled": bool(r["is_hadr_enabled"]),
                "mixed_mode_auth": not bool(r["integrated_security_only"]),
                "collation": text(r["collation_name"]),
                "is_sysadmin": bool(r["is_sysadmin"]),
                "view_server_state": bool(r["view_server_state"]),
                "view_any_definition": bool(r["view_any_definition"]),
                "cpu_count": sysinfo.get("cpu_count") if sysinfo else None,
                "physical_memory_bytes": int(num(sysinfo.get("physical_memory_kb"), 0) or 0) * 1024
                if sysinfo
                else None,
                "connected_database": text(r["current_db"]),
            },
        )
