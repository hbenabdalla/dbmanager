"""IBM Db2 for z/OS adapter (Phase 6) — a separate implementation, not Db2 LUW with a flag.

What a remote SQL client can honestly do on z/OS:

* connect through DRDA (``ibm_db``) — the client needs a Db2 Connect license file, or the
  subsystem must have Db2 Connect activated server-side (``db2connectactivate``);
* read the SYSIBM catalog, the real-time statistics tables and the authorization catalog;
* EXPLAIN into a ``PLAN_TABLE`` owned by CURRENT SQLID;
* derive REORG / RUNSTATS / COPY recommendations from real-time statistics (the DSNACCOX
  approach) — recommendations only, never executed.

What it cannot do through SQL and is classified as REQUIRES_EXTERNAL_MONITORING: subsystem
performance (IFI/IFCID, SMF, statistics and accounting traces), CPU/zIIP usage, buffer pool
activity, and RACF/ACF2/Top Secret identities. Threads (-DISPLAY THREAD via
ADMIN_COMMAND_DB2), the dynamic statement cache (EXPLAIN STMTCACHE ALL) and DSNZPARMs
(ADMIN_INFO_SYSPARM) are reachable through stored procedures but NOT IMPLEMENTED in this
release.
"""

from __future__ import annotations

import random
import re
import time
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.adapters.base import (
    FULL,
    NOT_IMPLEMENTED,
    NOT_SUPPORTED,
    PARTIAL,
    REQUIRES_CONFIGURATION,
    REQUIRES_EXTERNAL_MONITORING,
    AdapterError,
    AdapterNotSupported,
    Capability,
    CapabilityMap,
    ColumnInfo,
    DatabaseInfo,
    DbUser,
    MetricSnapshot,
    ObjectDetail,
    ObjectInfo,
    Privilege,
    SchemaInfo,
    ServerInfo,
    StorageInfo,
    cap,
)
from app.adapters.db2.base import Db2Base, qid
from app.adapters.db2zos import sql as Q
from app.adapters.dbapi import jsonable, num, text, utc
from app.domain.plan_analysis import ExplainResult, PlanNode, analyze_plan

C = Capability
SYSTEM_CREATORS = ("SYSIBM", "SYSTOOLS", "SYSPROC", "SYSFUN", "SYSIBMADM", "DSNACC", "DSN8", "SYSADM")
ACCESS = {
    "R": "TABLE SCAN (tablespace scan)",
    "I": "INDEX SCAN",
    "I1": "ONE-FETCH INDEX SCAN",
    "N": "INDEX SCAN (IN-list)",
    "M": "MULTIPLE INDEX SCAN",
    "MX": "INDEX SCAN (multiple-index step)",
    "MI": "INDEX INTERSECTION",
    "MU": "INDEX UNION",
    "DI": "DOCID INTERSECTION",
    "DU": "DOCID UNION",
    "E": "DIRECT ROW ACCESS",
    "H": "HASH ACCESS",
    "P": "DPSI PAGE RANGE",
    "V": "BUFFERED INSERT",
    "O": "WORK FILE SCAN",
}
METHODS = {0: "", 1: "NESTED LOOP JOIN", 2: "MERGE SCAN JOIN", 3: "SORT", 4: "HYBRID JOIN"}
USER_AUTHS = {
    "sysadmauth": "SYSADM",
    "sysctrlauth": "SYSCTRL",
    "sysoprauth": "SYSOPR",
    "bindaddauth": "BINDADD",
    "createdbaauth": "CREATEDBA",
    "securityadmauth": "SECADM",
    "dataaccessauth": "DATAACCESS",
    "accessctrlauth": "ACCESSCTRL",
    "sqladmauth": "SQLADM",
    "explainauth": "EXPLAIN",
}
DB_AUTHS = {
    "dbadmauth": "DBADM",
    "dbctrlauth": "DBCTRL",
    "dbmaintauth": "DBMAINT",
    "createtabauth": "CREATETAB",
    "createtsauth": "CREATETS",
}
TAB_AUTHS = {
    "selectauth": "SELECT",
    "insertauth": "INSERT",
    "updateauth": "UPDATE",
    "deleteauth": "DELETE",
    "alterauth": "ALTER",
    "indexauth": "INDEX",
}
ADMIN = {"SYSADM", "SYSCTRL", "SECADM", "DATAACCESS", "ACCESSCTRL", "DBADM", "DBCTRL"}
EXTERNAL = (
    "subsystem performance data (IFI/IFCID traces, SMF) is collected by z/OS monitors such as OMEGAMON, not over SQL"
)


def _granted(v: Any) -> bool:
    return (text(v) or "").strip() in ("Y", "G")


def parse_zos_version(v: str | None) -> tuple[int, int, int]:
    """``DSNvvrrm`` → (version, release, modification); e.g. DSN13015 → (13, 1, 5)."""
    m = re.match(r"DSN(\d{2})(\d{2})(\d)", (v or "").strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


class Db2zOSAdapter(Db2Base):
    engine = "db2zos"
    display_name = "IBM Db2 for z/OS"
    default_port = 446
    driver = "ibm_db (DRDA; Db2 Connect license or server-side activation required)"
    implementation_phase = 6
    implemented = True
    support_level = "partial"
    support_note = (
        "Catalog inventory, authorization catalog, real-time-statistics recommendations and EXPLAIN over DRDA. "
        "Subsystem performance requires z/OS monitoring tools (IFI/SMF). DRDA access needs Db2 Connect licensing."
    )
    connection_options: ClassVar[list[dict[str, Any]]] = [
        {
            "key": "current_schema",
            "label": "Current schema",
            "type": "string",
            "help": "Default qualifier for unqualified names in the SQL workspace.",
        },
        {
            "key": "ssl_server_certificate",
            "label": "Server certificate file",
            "type": "string",
            "help": "Not needed when a CA certificate is uploaded with the server (AT-TLS secure port).",
        },
    ]
    licensable_features: ClassVar[list[dict[str, Any]]] = [
        {
            "key": "db2_connect",
            "label": "Db2 Connect",
            "default": False,
            "covers": "DRDA access from distributed clients to Db2 for z/OS",
            "used_by_db_manager": "Required for the connection itself; informational flag (the license file or server-side "
            "activation is what Db2 checks)",
        }
    ]
    static_capabilities: ClassVar[CapabilityMap] = {
        C.CONNECTION: cap(
            PARTIAL, "DRDA through ibm_db; REQUIRES a Db2 Connect license file on the client or server-side activation"
        ),
        C.SERVER_INFO: cap(FULL, "GETVARIABLE('SYSIBM.VERSION'), location, SSID, data sharing group"),
        C.DATABASES: cap(FULL, "SYSIBM.SYSDATABASE"),
        C.DATABASE_CREATE: cap(NOT_SUPPORTED, "storage groups / buffer pools are site decisions: not offered"),
        C.DATABASE_DROP: cap(NOT_SUPPORTED),
        C.SCHEMAS: cap(FULL, "object qualifiers from SYSIBM.SYSTABLES / SYSROUTINES"),
        C.OBJECTS: cap(FULL, "tables, views, aliases, indexes, routines, triggers, sequences"),
        C.OBJECT_DDL: cap(PARTIAL, "column list from SYSIBM.SYSCOLUMNS; full DDL needs ADB2GEN/DB2 Admin Tool"),
        C.USERS: cap(
            REQUIRES_EXTERNAL_MONITORING, "identities live in RACF/ACF2/Top Secret; catalog grantees are shown"
        ),
        C.ROLES: cap(PARTIAL, "trusted-context roles (SYSIBM.SYSROLES)"),
        C.PRIVILEGES: cap(FULL, "SYSIBM.SYSUSERAUTH / SYSDBAUTH / SYSTABAUTH"),
        C.SESSIONS: cap(NOT_IMPLEMENTED, "-DISPLAY THREAD through ADMIN_COMMAND_DB2 is not implemented"),
        C.SESSION_CANCEL: cap(NOT_IMPLEMENTED, "-CANCEL THREAD is not implemented"),
        C.SESSION_TERMINATE: cap(NOT_IMPLEMENTED, "-CANCEL THREAD is not implemented"),
        C.LOCKS: cap(NOT_IMPLEMENTED, "-DISPLAY DATABASE LOCKS is not implemented"),
        C.PERFORMANCE_METRICS: cap(REQUIRES_EXTERNAL_MONITORING, EXTERNAL),
        C.CPU_METRICS: cap(REQUIRES_EXTERNAL_MONITORING, "CPU and zIIP usage come from SMF/RMF"),
        C.MEMORY_METRICS: cap(REQUIRES_EXTERNAL_MONITORING, EXTERNAL),
        C.WAIT_EVENTS: cap(REQUIRES_EXTERNAL_MONITORING, "class 3 suspension times come from accounting traces"),
        C.QUERY_STATISTICS: cap(NOT_IMPLEMENTED, "dynamic statement cache (EXPLAIN STMTCACHE ALL) is not implemented"),
        C.QUERY_HISTORY: cap(NOT_IMPLEMENTED),
        C.EXPLAIN: cap(PARTIAL, "EXPLAIN PLAN into PLAN_TABLE (owned by CURRENT SQLID); access path only"),
        C.EXPLAIN_ANALYZE: cap(NOT_SUPPORTED, "actuals require accounting/IFCID traces"),
        C.REPLICATION: cap(REQUIRES_EXTERNAL_MONITORING, "data sharing, GDPS and replication are monitored on z/OS"),
        C.CONFIGURATION: cap(NOT_IMPLEMENTED, "DSNZPARMs (ADMIN_INFO_SYSPARM) are not read"),
        C.STORAGE: cap(PARTIAL, "real-time statistics (SYSIBM.SYSTABLESPACESTATS); DASD capacity is external"),
        C.TABLESPACES: cap(FULL, "SYSIBM.SYSTABLESPACESTATS"),
        C.SECURITY_SCAN: cap(PARTIAL, "catalog authorities only; external security manager not visible"),
        C.LOGS: cap(REQUIRES_EXTERNAL_MONITORING, "MSTR/DBM1 job logs and SYSLOG are on z/OS"),
        C.SQL_WORKSPACE: cap(FULL),
        C.READ_ONLY_TRANSACTIONS: cap(PARTIAL, "classifier + privileges + rollback"),
        C.ADMINISTRATION: cap(NOT_IMPLEMENTED, "administration on z/OS goes through change management, not DB Manager"),
        C.RECOMMENDATIONS: cap(PARTIAL, "REORG / RUNSTATS / COPY recommendations from real-time statistics"),
    }
    engine_capabilities: ClassVar[CapabilityMap] = static_capabilities

    # ---- discovery -----------------------------------------------------------------------------
    async def _server(self) -> dict[str, Any]:
        if self._info is None:
            r = await self._try_one(Q.SERVER_INFO)
            if r is None:
                r = await self._one("SELECT CURRENT SERVER AS LOCATION, USER AS AUTHID FROM SYSIBM.SYSDUMMY1")
            info: dict[str, Any] = dict(r or {})
            v = parse_zos_version(text(info.get("version")))
            info["major"], info["release"], info["mod"] = v
            info["version_num"] = v[0] * 1_000_000 + v[1] * 10_000 + v[2] * 100
            compat = await self._try_one(Q.APP_COMPAT)
            info["applcompat"] = text(compat["applcompat"]) if compat else None
            info["db_name"] = text(info.get("location"))
            info["cur_user"] = text(info.get("authid"))
            self._info = info
        return self._info

    async def discover_capabilities(self) -> CapabilityMap:
        caps = dict(self.capability_profile())
        await self._server()
        caps[C.CONNECTION] = cap(FULL, "connected over DRDA (Db2 Connect licensing satisfied)")
        if await self._try_one(Q.PLAN_TABLE_EXISTS) is None:
            caps[C.EXPLAIN] = cap(
                REQUIRES_CONFIGURATION, "create PLAN_TABLE (and DSN_STATEMNT_TABLE) under CURRENT SQLID (DSNTESC job)"
            )
        if await self._try(Q.RTS_TABLESPACES.replace("FETCH FIRST 500", "FETCH FIRST 1")) is None:
            caps[C.STORAGE] = cap(PARTIAL, "SELECT on SYSIBM.SYSTABLESPACESTATS is missing")
            caps[C.RECOMMENDATIONS] = cap(
                PARTIAL, "real-time statistics are not readable: grant SELECT on SYSIBM.SYSTABLESPACESTATS"
            )
        return caps

    async def get_server_info(self) -> ServerInfo:
        r = await self._server()
        return ServerInfo(
            engine=self.engine,
            version=f"{r['major']}.{r['release']}.{r['mod']}" if r["major"] else (text(r.get("version")) or ""),
            version_num=r["version_num"],
            edition=f"Db2 {r['major']} for z/OS" if r["major"] else "Db2 for z/OS",
            current_user=r["cur_user"],
            hostname=self.spec.host,
            extra={
                "location": text(r.get("location")),
                "ssid": text(r.get("ssid")),
                "data_sharing_group": text(r.get("dsgroup")) or None,
                "member": text(r.get("member_name")) or None,
                "current_sqlid": text(r.get("sqlid")),
                "application_compatibility": r.get("applcompat"),
                "product_code": text(r.get("version")),
            },
        )

    # ---- inventory --------------------------------------------------------------------------------
    async def list_databases(self) -> list[DatabaseInfo]:
        space = {
            text(r["dbname"]) or "": int(num(r["space_kb"], 0) or 0) * 1024
            for r in await self._try(Q.DATABASE_SPACE) or []
        }
        out = []
        for r in await self._query(Q.DATABASES):
            name = text(r["name"]) or ""
            out.append(
                DatabaseInfo(
                    name=name,
                    owner=text(r["creator"]),
                    size_bytes=space.get(name),
                    is_system=name.startswith("DSN"),
                    encoding={"E": "EBCDIC", "A": "ASCII", "U": "Unicode"}.get(
                        text(r["encoding_scheme"]) or "", text(r["encoding_scheme"])
                    ),
                    tablespace=text(r["stgroup"]),
                    extra=jsonable(
                        {
                            "buffer_pool": r["bpool"],
                            "type": {"W": "work file"}.get(text(r["type"]) or "", "user"),
                            "created": r["createdts"],
                        }
                    ),
                )
            )
        return out

    async def list_schemas(self, database: str) -> list[SchemaInfo]:
        counts: dict[str, dict[str, int]] = {}
        kinds = {"T": "tables", "V": "views", "A": "aliases", "M": "mqts", "G": "temporary_tables"}
        for r in await self._query(Q.SCHEMAS):
            k = kinds.get(text(r["type"]) or "")
            if k:
                d = counts.setdefault(text(r["creator"]) or "", {})
                d[k] = d.get(k, 0) + int(r["n"] or 0)
        for r in await self._try(Q.ROUTINE_SCHEMAS) or []:
            k = "procedures" if text(r["routinetype"]) == "P" else "functions"
            d = counts.setdefault(text(r["schema"]) or "", {})
            d[k] = d.get(k, 0) + int(r["n"] or 0)
        return [
            SchemaInfo(name=name, owner=name, is_system=name.startswith(SYSTEM_CREATORS), object_counts=c)
            for name, c in sorted(counts.items())
        ]

    async def list_objects(self, database: str, schema: str, object_type: str | None = None) -> list[ObjectInfo]:
        types = {"table": "'T'", "view": "'V'", "alias": "'A'", "materialized_view": "'M'"}
        known = {*types, "index", "procedure", "function", "trigger", "sequence"}
        if object_type and object_type not in known:
            raise AdapterNotSupported(Capability.OBJECTS, f"object type {object_type!r} is not listed for Db2 for z/OS")
        want = lambda k: object_type in (None, k)  # noqa: E731
        out: list[ObjectInfo] = []
        tsel = [v for k, v in types.items() if want(k)]
        if tsel:
            kind_of = {"T": "table", "V": "view", "A": "alias", "M": "materialized_view"}
            for r in await self._query(Q.TABLES.format(types=", ".join(tsel)), [schema]):
                out.append(
                    ObjectInfo(
                        name=text(r["name"]) or "",
                        schema_name=schema,
                        type=kind_of.get(text(r["type"]) or "", "table"),
                        row_estimate=int(num(r["cardf"], -1) or -1) if (num(r["cardf"], -1) or -1) >= 0 else None,
                        extra=jsonable(
                            {
                                "database": r["dbname"],
                                "tablespace": r["tsname"],
                                "stats_time": r["statstime"],
                                "created": r["createdts"],
                            }
                        ),
                    )
                )
        if want("index"):
            for r in await self._query(Q.INDEXES.format(table_filter=""), [schema]):
                out.append(
                    ObjectInfo(
                        name=text(r["name"]) or "",
                        schema_name=schema,
                        type="index",
                        parent=text(r["tbname"]),
                        extra=jsonable(
                            {
                                "unique": text(r["uniquerule"]) in ("U", "P", "C", "N", "R"),
                                "clustering": text(r["clustering"]) == "Y",
                                "index_space": r["indexspace"],
                            }
                        ),
                    )
                )
        rsel = [v for k, v in (("procedure", "'P'"), ("function", "'F'")) if want(k)]
        if rsel:
            for r in await self._query(Q.ROUTINES.format(types=", ".join(rsel)), [schema]):
                out.append(
                    ObjectInfo(
                        name=text(r["name"]) or "",
                        schema_name=schema,
                        type="procedure" if text(r["routinetype"]) == "P" else "function",
                        extra=jsonable({"origin": r["origin"], "created": r["createdts"]}),
                    )
                )
        if want("trigger"):
            for r in await self._try(Q.TRIGGERS, [schema]) or []:
                out.append(
                    ObjectInfo(
                        name=text(r["name"]) or "",
                        schema_name=schema,
                        type="trigger",
                        parent=text(r["tbname"]),
                        extra=jsonable({"timing": r["trigtime"], "event": r["trigevent"]}),
                    )
                )
        if want("sequence"):
            for r in await self._try(Q.SEQUENCES, [schema]) or []:
                out.append(
                    ObjectInfo(
                        name=text(r["name"]) or "",
                        schema_name=schema,
                        type="sequence",
                        extra=jsonable({"start": r["start"], "increment": r["increment"]}),
                    )
                )
        return out

    async def get_object_detail(self, database: str, schema: str, name: str, object_type: str) -> ObjectDetail:
        row = await self._one(Q.TABLE_ROW, [schema, name])
        if row is None:
            raise AdapterNotSupported(Capability.OBJECT_DDL, "details are available for tables and views only")
        cols = []
        for c in await self._query(Q.COLUMNS, [schema, name]):
            t = text(c["coltype"]) or ""
            if t in ("VARCHAR", "CHAR", "VARG", "GRAPHIC", "BINARY", "VARBIN"):
                t += f"({c['length']})"
            elif t == "DECIMAL":
                t += f"({c['length']},{c['scale']})"
            cols.append(
                ColumnInfo(
                    name=text(c["name"]) or "", data_type=t, nullable=text(c["nulls"]) == "Y", position=int(c["colno"])
                )
            )
        idx = [
            jsonable(dict(r))
            for r in await self._query(Q.INDEXES.format(table_filter="AND TBNAME = ?"), [schema, name])
        ]
        return ObjectDetail(
            object=ObjectInfo(name=name, schema_name=schema, type=object_type),
            columns=cols,
            indexes=idx,
            statistics=jsonable({k: row[k] for k in ("dbname", "tsname", "cardf", "npagesf", "statstime")}),
            definition=None,
            definition_note="DDL generation on z/OS requires ADB2GEN / Db2 Administration Tool; columns and indexes are shown",
        )

    async def _user_auths(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in await self._try(Q.USERAUTH) or []:
            out.setdefault(text(r["grantee"]) or "", []).extend(
                a for col, a in USER_AUTHS.items() if _granted(r.get(col))
            )
        for r in await self._try(Q.DBAUTH) or []:
            out.setdefault(text(r["grantee"]) or "", []).extend(
                f"{a} ON {text(r['name'])}" for col, a in DB_AUTHS.items() if _granted(r.get(col))
            )
        return out

    async def list_users(self) -> list[DbUser]:
        auths = await self._user_auths()
        return [
            DbUser(
                name=g,
                can_login=True,
                is_superuser="SYSADM" in a,
                attributes={
                    "authorities": sorted(set(a)),
                    "identity": "external security manager (RACF/ACF2/Top Secret)",
                },
                is_system=g in ("PUBLIC", "SYSIBM", "SYSADM") or g.startswith("SYS"),
                admin_reasons=sorted({x.split(" ON ")[0] for x in a} & ADMIN),
            )
            for g, a in sorted(auths.items())
        ]

    async def list_roles(self) -> list[DbUser]:
        return [
            DbUser(
                name=text(r["name"]) or "",
                can_login=False,
                attributes=jsonable({"definer": r["definer"], "created": r["createdts"]}),
            )
            for r in await self._try(Q.ROLES) or []
        ]

    async def get_privileges(
        self, database: str | None = None, grantee: str | None = None, limit: int = 2000
    ) -> list[Privilege]:
        out: list[Privilege] = []
        for g, a in (await self._user_auths()).items():
            for x in a:
                name, _, obj = x.partition(" ON ")
                out.append(
                    Privilege(
                        grantee=g,
                        object_type="database" if obj else "system",
                        object_name=obj or "SUBSYSTEM",
                        privilege=name,
                    )
                )
        for r in await self._query(Q.TABAUTH.format(limit=int(limit))):
            for col, p in TAB_AUTHS.items():
                if _granted(r.get(col)):
                    out.append(
                        Privilege(
                            grantee=text(r["grantee"]) or "",
                            object_type="table",
                            object_name=f"{text(r['tcreator'])}.{text(r['ttname'])}",
                            privilege=p,
                            grantor=text(r["grantor"]),
                            grantable=text(r.get(col)) == "G",
                        )
                    )
        if grantee:
            out = [p for p in out if p.grantee.upper() == grantee.upper()]
        return out[:limit]

    # ---- monitoring: honest boundaries -------------------------------------------------------------
    async def get_performance_metrics(self, slow: bool = True) -> MetricSnapshot:
        snap = MetricSnapshot(collected_at=datetime.now(UTC))
        snap.gauges["availability.up"] = 1.0
        for k in ("connections.total", "cpu.percent", "cache.hit_ratio", "transactions.per_sec"):
            snap.unavailable[k] = EXTERNAL
        if slow:
            space = await self._try(Q.DATABASE_SPACE)
            if space:
                snap.gauges["storage.database_bytes"] = float(
                    sum(int(num(r["space_kb"], 0) or 0) for r in space) * 1024
                )
        return snap

    async def get_storage_metrics(self) -> StorageInfo:
        top = await self._try(Q.TOP_SPACES)
        return StorageInfo(
            total_bytes=sum(int(num(r["space_kb"], 0) or 0) * 1024 for r in top or []) or None,
            databases=[{"name": d.name, "size_bytes": d.size_bytes} for d in await self.list_databases()],
            tablespaces=[
                {
                    "name": f"{text(r['dbname'])}.{text(r['name'])}",
                    "used_bytes": int(num(r["space_kb"], 0) or 0) * 1024,
                    "rows": jsonable(r["totalrows"]),
                }
                for r in top or []
            ],
            notes=["sizes from real-time statistics (SPACE, KB); volume/DASD capacity is managed on z/OS"]
            + ([] if top is not None else ["SELECT on SYSIBM.SYSTABLESPACESTATS is missing"]),
        )

    # ---- evidence ---------------------------------------------------------------------------------
    async def get_security_context(self) -> dict[str, Any]:
        info = await self._server()
        auths = await self._user_auths()

        def holders(a: str) -> list[str]:
            return sorted(g for g, x in auths.items() if a in x)

        public_tab = await self._try_one(Q.PUBLIC_TABLE_GRANTS)
        contexts = await self._try(Q.TRUSTED_CONTEXTS)
        return {
            "engine": self.engine,
            "version": text(info.get("version")),
            "version_num": info["version_num"],
            "sysadm_holders": holders("SYSADM"),
            "sysctrl_holders": holders("SYSCTRL"),
            "secadm_holders": holders("SECADM"),
            "dataaccess_holders": holders("DATAACCESS"),
            "public_authorities": sorted(set(auths.get("PUBLIC", []))),
            "public_table_grants": int(public_tab["n"] or 0) if public_tab else None,
            "trusted_contexts": [jsonable(dict(r)) for r in contexts] if contexts is not None else None,
            "external_security_note": "RACF/ACF2/Top Secret profiles (DSNR, MDSNTB, ...) are not visible over SQL",
            "roles": [u.model_dump(mode="json") for u in await self.list_users()],
        }

    async def get_tuning_context(self) -> dict[str, Any]:
        rows = await self._try(Q.RTS_TABLESPACES) or []
        now = datetime.now(UTC)
        reorg, runstats, copy = [], [], []
        for r in rows:
            total = num(r["totalrows"], 0) or 0
            name = f"{text(r['dbname'])}.{text(r['name'])}" + (
                f" part {r['partition']}" if int(r["partition"] or 0) else ""
            )
            changes = (num(r["reorginserts"], 0) or 0) + (num(r["reorgdeletes"], 0) or 0)
            unclust = num(r["reorgunclustins"], 0) or 0
            if (
                total > 0
                and (100 * changes / total > 25 or 100 * unclust / total > 10)
                or (num(r["reorgmassdelete"], 0) or 0) > 0
            ):
                reorg.append(
                    jsonable(
                        {
                            "object": name,
                            "totalrows": total,
                            "reorg_changes": changes,
                            "unclustered_inserts": unclust,
                            "massdelete": r["reorgmassdelete"],
                            "last_reorg": r["reorglasttime"],
                        }
                    )
                )
            schanges = sum(num(r[k], 0) or 0 for k in ("statsinserts", "statsdeletes", "statsupdates"))
            if r["statslasttime"] is None or (total > 0 and 100 * schanges / total > 20):
                runstats.append(
                    jsonable(
                        {
                            "object": name,
                            "totalrows": total,
                            "changes_since_runstats": schanges,
                            "last_runstats": r["statslasttime"],
                        }
                    )
                )
            last_copy = utc(r["copylasttime"])
            if (num(r["copyupdatedpages"], 0) or 0) > 0 and (last_copy is None or (now - last_copy).days > 7):
                copy.append(
                    jsonable({"object": name, "updated_pages": r["copyupdatedpages"], "last_copy": r["copylasttime"]})
                )
        return {
            "engine": self.engine,
            "rts_objects_scanned": len(rows),
            "reorg_recommended": reorg[:50],
            "runstats_recommended": runstats[:50],
            "copy_recommended": copy[:50],
            "thresholds": "DSNACCOX-style defaults: REORG > 25% inserts+deletes or > 10% unclustered inserts; RUNSTATS > 20% changes; COPY > 7 days with updates",
        }

    # ---- explain ---------------------------------------------------------------------------------
    def _explain_sync(self, stmt: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None, float]:
        conn = self._get_conn(None)
        cur = conn.cursor()
        queryno = random.randint(1_000_000, 2_000_000_000)  # noqa: S311 - an identifier, not a secret
        start = time.perf_counter()

        def rows() -> list[dict[str, Any]]:
            cols = [str(d[0]).lower() for d in cur.description or []]
            return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]

        try:
            cur.execute(f"EXPLAIN PLAN SET QUERYNO = {queryno} FOR {stmt}")
            cur.execute(Q.PLAN_ROWS, (queryno,))
            plan = rows()
            cost = None
            try:
                cur.execute(Q.STATEMENT_COST, (queryno,))
                c = rows()
                cost = c[0] if c else None
            except Exception:  # noqa: BLE001 - DSN_STATEMNT_TABLE is optional
                cost = None
            return plan, cost, (time.perf_counter() - start) * 1000
        finally:
            try:
                conn.rollback()
            finally:
                cur.close()

    async def explain_query(self, sql: str, database: str | None = None, analyze: bool = False) -> ExplainResult:
        if analyze:
            raise AdapterError(
                "actual execution statistics on z/OS need accounting/IFCID traces (not available over SQL)"
            )
        stmt = sql.strip().rstrip(";").strip()
        if not re.match(r"^\s*(SELECT|WITH|INSERT|UPDATE|DELETE|MERGE)\b", stmt, re.I):
            raise AdapterError("only SELECT, WITH, INSERT, UPDATE, DELETE and MERGE can be explained")
        if await self._try_one(Q.PLAN_TABLE_EXISTS) is None:
            raise AdapterError("PLAN_TABLE does not exist under CURRENT SQLID (create it with the DSNTESC sample job)")
        plan, cost, elapsed = await self._run(self._explain_sync, stmt)
        if not plan:
            raise AdapterError("EXPLAIN produced no PLAN_TABLE rows")
        cards: dict[tuple[str, str], float | None] = {}
        for p in plan:
            key = (text(p["creator"]) or "", text(p["tname"]) or "")
            if key[1] and key not in cards:
                row = await self._try_one(Q.TABLE_CARD, list(key))
                cards[key] = num(row["cardf"]) if row else None
        root = PlanNode(
            id=0, operator="QUERY", total_cost=num(cost["procsu"]) if cost else None, raw={"engine_warnings": []}
        )
        blocks: dict[int, PlanNode] = {}
        nid = 1
        for p in plan:
            qb = int(p["qblockno"] or 1)
            if qb not in blocks:
                blocks[qb] = PlanNode(
                    id=nid,
                    operator=f"QUERY BLOCK {qb} ({text(p['qblock_type']) or 'SELECT'})",
                    raw={"engine_warnings": []},
                )
                root.children.append(blocks[qb])
                nid += 1
            access = ACCESS.get((text(p["accesstype"]) or "").strip(), text(p["accesstype"]) or "")
            method = METHODS.get(int(p["method"] or 0), "")
            sorts = [k for k in ("sortn_join", "sortc_uniq", "sortc_orderby", "sortc_groupby") if text(p.get(k)) == "Y"]
            op = " / ".join(x for x in (method, access) if x) or "STEP"
            key = (text(p["creator"]) or "", text(p["tname"]) or "")
            node = PlanNode(
                id=nid,
                operator=op,
                relation=f"{key[0]}.{key[1]}" if key[1] else None,
                index=f"{text(p['accesscreator'])}.{text(p['accessname'])}" if text(p.get("accessname")) else None,
                estimated_rows=cards.get(key) if access.startswith("TABLE SCAN") else None,
                detail=", ".join(
                    x
                    for x in (
                        f"matching columns {p['matchcols']}" if p.get("matchcols") else "",
                        "index only" if text(p.get("indexonly")) == "Y" else "",
                        f"prefetch {text(p['prefetch'])}" if (text(p.get("prefetch")) or "").strip() else "",
                        f"sorts: {', '.join(sorts)}" if sorts else "",
                    )
                    if x
                )
                or None,
                raw={**jsonable(p), "engine_warnings": []},
            )
            blocks[qb].children.append(node)
            nid += 1
        return ExplainResult(
            engine=self.engine,
            mode="estimate",
            executed=False,
            root=root,
            issues=analyze_plan(root, analyzed=False),
            total_cost=root.total_cost,
            planning_ms=round(elapsed, 2),
            raw={"plan_table": jsonable(plan), "statement_cost": jsonable(cost)},
            notes=[
                "PLAN_TABLE rows describe the access path in join order; they were rolled back after reading.",
                "Cost (service units) comes from DSN_STATEMNT_TABLE when it exists.",
            ],
        )

    def build_admin(self, action: str, params: dict[str, Any]) -> Any:
        raise AdapterNotSupported(Capability.ADMINISTRATION, "administration actions are not offered for Db2 for z/OS")


__all__ = ["Db2zOSAdapter", "parse_zos_version", "qid"]
