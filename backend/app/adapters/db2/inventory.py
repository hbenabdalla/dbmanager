"""Db2 LUW inventory from the SYSCAT catalog views.

A Db2 connection is to one database; users and groups are authenticated outside Db2 (OS, LDAP,
Kerberos), so "users" are the authorization IDs that appear in the catalog's grant tables."""

from __future__ import annotations

from typing import Any

from app.adapters.base import (
    AdapterNotSupported,
    Capability,
    ColumnInfo,
    DatabaseInfo,
    DbUser,
    ObjectDetail,
    ObjectInfo,
    Privilege,
    SchemaInfo,
)
from app.adapters.db2 import sql as Q
from app.adapters.db2.base import Db2Base, qid
from app.adapters.dbapi import jsonable, num, text

SYSTEM_SCHEMAS = {
    "SYSIBM",
    "SYSCAT",
    "SYSFUN",
    "SYSSTAT",
    "SYSPROC",
    "SYSIBMADM",
    "SYSTOOLS",
    "SYSPUBLIC",
    "NULLID",
    "SQLJ",
    "SYSIBMINTERNAL",
    "SYSIBMTS",
    "SYSHADOOP",
    "SYSDEBUG",
    "SYSCATV82",
}
TABLE_TYPES = {
    "table": "'T', 'U', 'H'",
    "view": "'V', 'W'",
    "materialized_view": "'S'",
    "alias": "'A'",
    "nickname": "'N'",
}
KIND_OF = {
    "T": "table",
    "U": "table",
    "H": "table",
    "V": "view",
    "W": "view",
    "S": "materialized_view",
    "A": "alias",
    "N": "nickname",
    "G": "table",
}
#: SYSCAT.DBAUTH column → authority name
DB_AUTHORITIES = {
    "bindaddauth": "BINDADD",
    "connectauth": "CONNECT",
    "createtabauth": "CREATETAB",
    "dbadmauth": "DBADM",
    "externalroutineauth": "CREATE_EXTERNAL_ROUTINE",
    "implschemaauth": "IMPLICIT_SCHEMA",
    "loadauth": "LOAD",
    "nofenceauth": "CREATE_NOT_FENCED_ROUTINE",
    "quiesceconnectauth": "QUIESCE_CONNECT",
    "securityadmauth": "SECADM",
    "sqladmauth": "SQLADM",
    "wlmadmauth": "WLMADM",
    "explainauth": "EXPLAIN",
    "dataaccessauth": "DATAACCESS",
    "accessctrlauth": "ACCESSCTRL",
    "createsecureauth": "CREATE_SECURE_OBJECT",
}
ADMIN_AUTHORITIES = {
    "DBADM": "DBADM (database administration)",
    "SECADM": "SECADM (security administration)",
    "DATAACCESS": "DATAACCESS (reads all data)",
    "ACCESSCTRL": "ACCESSCTRL (grants privileges)",
    "CREATE_NOT_FENCED_ROUTINE": "CREATE_NOT_FENCED_ROUTINE (code in the engine)",
    "SYSADM": "SYSADM (instance)",
    "SYSCTRL": "SYSCTRL (instance)",
    "SYSMAINT": "SYSMAINT (instance)",
}
TAB_PRIVS = {
    "controlauth": "CONTROL",
    "alterauth": "ALTER",
    "deleteauth": "DELETE",
    "indexauth": "INDEX",
    "insertauth": "INSERT",
    "refauth": "REFERENCES",
    "selectauth": "SELECT",
    "updateauth": "UPDATE",
}
GRANTEE_TYPES = {"U": "user", "G": "group", "R": "role"}


class Db2InventoryMixin(Db2Base):
    async def list_databases(self) -> list[DatabaseInfo]:
        info = await self._server()
        ts = await self._try(Q.TABLESPACES) or []
        size = sum(int(num(t["used_pages"], 0) or 0) * int(num(t["tbsp_page_size"], 0) or 0) for t in ts)
        return [
            DatabaseInfo(
                name=text(info.get("db_name")) or self.spec.database or "",
                size_bytes=size or None,
                allow_connections=True,
                accessible=True,
                extra={"instance": text(info["inst"].get("inst_name")), "members": info["inst"].get("num_members")},
            )
        ]

    async def list_schemas(self, database: str) -> list[SchemaInfo]:
        out = []
        for r in await self._query(Q.SCHEMAS):
            name = text(r["schemaname"]) or ""
            out.append(
                SchemaInfo(
                    name=name,
                    owner=text(r["owner"]),
                    is_system=name in SYSTEM_SCHEMAS or name.startswith("SYS"),
                    object_counts={
                        k: int(r[k] or 0)
                        for k in (
                            "tables",
                            "views",
                            "mqts",
                            "indexes",
                            "procedures",
                            "functions",
                            "triggers",
                            "sequences",
                        )
                    },
                )
            )
        return out

    async def list_objects(self, database: str, schema: str, object_type: str | None = None) -> list[ObjectInfo]:
        known = {*TABLE_TYPES, "index", "procedure", "function", "trigger", "sequence"}
        if object_type and object_type not in known:
            raise AdapterNotSupported(Capability.OBJECTS, f"object type {object_type!r} is not listed for Db2")
        out: list[ObjectInfo] = []
        want = lambda k: object_type in (None, k)  # noqa: E731
        ttypes = [v for k, v in TABLE_TYPES.items() if want(k)]
        if ttypes:
            for r in await self._query(Q.TABLES.format(types=", ".join(ttypes)), [schema]):
                kind = KIND_OF.get(text(r["type"]) or "", "table")
                fpages, page = int(num(r["fpages"], -1) or -1), int(num(r["pagesize"], 0) or 0)
                out.append(
                    ObjectInfo(
                        name=text(r["tabname"]) or "",
                        schema_name=schema,
                        type=kind,
                        size_bytes=fpages * page if fpages > 0 and page and kind == "table" else None,
                        row_estimate=int(r["card"]) if r["card"] is not None and int(r["card"]) >= 0 else None,
                        extra=jsonable(
                            {
                                "status": {"N": "normal", "C": "check pending", "X": "inoperative"}.get(
                                    text(r["status"]) or "", r["status"]
                                ),
                                "tablespace": r["tbspace"],
                                "stats_time": r["stats_time"],
                                "created": r["create_time"],
                                "altered": r["alter_time"],
                                "organization": {"R": "row", "C": "column"}.get(
                                    text(r["tableorg"]) or "", r["tableorg"]
                                ),
                                "compression": r["compression"],
                                "size_source": "catalog statistics (FPAGES × page size)",
                            }
                        ),
                    )
                )
        if want("index"):
            for r in await self._query(Q.INDEXES.format(table_filter=""), [schema]):
                out.append(
                    ObjectInfo(
                        name=text(r["indname"]) or "",
                        schema_name=schema,
                        type="index",
                        parent=text(r["tabname"]),
                        extra=jsonable(
                            {
                                "unique": text(r["uniquerule"]) in ("U", "P"),
                                "primary": text(r["uniquerule"]) == "P",
                                "index_type": r["indextype"],
                                "columns": r["colnames"],
                                "levels": r["nlevels"],
                                "cluster_ratio": r["clusterratio"],
                                "stats_time": r["stats_time"],
                            }
                        ),
                    )
                )
        rtypes = [v for k, v in (("procedure", "'P'"), ("function", "'F'")) if want(k)]
        if rtypes:
            for r in await self._query(Q.ROUTINES.format(types=", ".join(rtypes)), [schema]):
                out.append(
                    ObjectInfo(
                        name=text(r["routinename"]) or "",
                        schema_name=schema,
                        type="procedure" if text(r["routinetype"]) == "P" else "function",
                        extra=jsonable(
                            {
                                "specific_name": r["specificname"],
                                "language": r["language"],
                                "valid": r["valid"],
                                "created": r["create_time"],
                            }
                        ),
                    )
                )
        if want("trigger"):
            for r in await self._query(Q.TRIGGERS, [schema]):
                out.append(
                    ObjectInfo(
                        name=text(r["trigname"]) or "",
                        schema_name=schema,
                        type="trigger",
                        parent=text(r["tabname"]),
                        extra=jsonable({"timing": r["trigtime"], "event": r["trigevent"], "valid": r["valid"]}),
                    )
                )
        if want("sequence"):
            for r in await self._query(Q.SEQUENCES, [schema]):
                out.append(
                    ObjectInfo(
                        name=text(r["seqname"]) or "",
                        schema_name=schema,
                        type="sequence",
                        extra=jsonable({"start": r["start"], "increment": r["increment"], "cache": r["cache"]}),
                    )
                )
        return out

    async def get_object_detail(self, database: str, schema: str, name: str, object_type: str) -> ObjectDetail:
        args = [schema, name]
        definition, note, cols, idx, cons, trg = None, None, [], [], [], []
        stats: dict[str, Any] = {}
        if object_type in ("table", "view", "materialized_view", "alias", "nickname"):
            row = await self._one(Q.TABLE_ROW, args)
            if row is None:
                raise AdapterNotSupported(Capability.OBJECT_DDL, f"{schema}.{name} not found")
            for c in await self._query(Q.COLUMNS, args):
                t = text(c["typename"]) or ""
                if t in ("VARCHAR", "CHARACTER", "VARGRAPHIC", "GRAPHIC", "BINARY", "VARBINARY"):
                    t += f"({c['length']})"
                elif t == "DECIMAL":
                    t += f"({c['length']},{c['scale']})"
                cols.append(
                    ColumnInfo(
                        name=text(c["colname"]) or "",
                        data_type=t,
                        nullable=text(c["nulls"]) == "Y",
                        default=text(c["default"]),
                        position=int(c["colno"]) + 1,
                        comment=text(c["remarks"]),
                    )
                )
            idx = [jsonable(dict(r)) for r in await self._query(Q.INDEXES.format(table_filter="AND TABNAME = ?"), args)]
            cons = [jsonable(dict(r)) for r in await self._try(Q.CONSTRAINTS, args) or []]
            cons += [jsonable({**dict(r), "type": "F"}) for r in await self._try(Q.REFERENCES, args) or []]
            trg = [jsonable(dict(r)) for r in await self._query(Q.TRIGGERS_OF, args)]
            stats = jsonable({k: row[k] for k in ("card", "npages", "fpages", "overflow", "stats_time", "tbspace")})
            if text(row["type"]) in ("V", "W", "S"):
                v = await self._try_one(Q.VIEW_TEXT, args)
                definition = text(v["text"]) if v else None
            else:
                lines = [
                    f"    {qid(c.name)} {c.data_type}{'' if c.nullable else ' NOT NULL'}{(' WITH DEFAULT ' + c.default) if c.default else ''}"
                    for c in cols
                ]
                definition = (
                    f"CREATE TABLE {qid(schema)}.{qid(name)} (\n"
                    + ",\n".join(lines)
                    + "\n)"
                    + (f" IN {qid(text(row['tbspace']) or '')}" if row["tbspace"] else "")
                    + ";"
                )
                note = "reconstructed from SYSCAT.COLUMNS (use db2look for complete DDL with storage options)"
        elif object_type in ("procedure", "function"):
            r = await self._try_one(Q.ROUTINE_TEXT, args)
            definition = text(r["text"]) if r else None
            note = None if definition else "external routine or text not stored in SYSCAT.ROUTINES"
        elif object_type == "trigger":
            r = await self._try_one(Q.TRIGGER_TEXT, args)
            definition = text(r["text"]) if r else None
        else:
            note = "definition not available for this object type"
        return ObjectDetail(
            object=ObjectInfo(name=name, schema_name=schema, type=object_type),
            columns=cols,
            indexes=idx,
            constraints=cons,
            triggers=trg,
            statistics=stats,
            definition=definition,
            definition_note=note,
        )

    # ---- authorization ----------------------------------------------------------------------------
    async def _db_authorities(self) -> dict[tuple[str, str], list[str]]:
        out: dict[tuple[str, str], list[str]] = {}
        for r in await self._query(Q.DBAUTH):
            key = (text(r["grantee"]) or "", text(r["granteetype"]) or "U")
            out.setdefault(key, []).extend(a for col, a in DB_AUTHORITIES.items() if text(r[col]) == "Y")
        return out

    async def _role_edges(self) -> list[dict[str, Any]]:
        return [dict(r) for r in await self._try(Q.ROLEAUTH) or []]

    async def list_users(self) -> list[DbUser]:
        auths = await self._db_authorities()
        edges = await self._role_edges()
        info = await self._server()
        me = text(info.get("cur_user"))
        out = []
        for r in await self._query(Q.GRANTEES):
            name, gtype = text(r["grantee"]) or "", text(r["granteetype"]) or "U"
            if gtype == "R":
                continue
            a = sorted(set(auths.get((name, gtype), [])))
            roles = sorted(
                {
                    text(e["rolename"]) or ""
                    for e in edges
                    if text(e["grantee"]) == name and text(e["granteetype"]) == gtype
                }
            )
            out.append(
                DbUser(
                    name=name if gtype == "U" else f"{name} (group)",
                    can_login="CONNECT" in a or name == "PUBLIC",
                    is_superuser="DBADM" in a and "DATAACCESS" in a,
                    attributes={
                        "type": GRANTEE_TYPES.get(gtype, gtype),
                        "database_authorities": a,
                        "authentication": "external (operating system / LDAP / Kerberos)",
                        **({"authorities_of_current_session": info["authorities"]} if name == me else {}),
                    },
                    member_of=roles,
                    is_system=name in ("PUBLIC", "SYSIBM", "SYSIBMINTERNAL"),
                    admin_reasons=[ADMIN_AUTHORITIES[x] for x in a if x in ADMIN_AUTHORITIES],
                )
            )
        return out

    async def list_roles(self) -> list[DbUser]:
        auths = await self._db_authorities()
        edges = await self._role_edges()
        out = []
        for r in await self._query(Q.ROLES):
            name = text(r["rolename"]) or ""
            a = sorted(set(auths.get((name, "R"), [])))
            out.append(
                DbUser(
                    name=name,
                    can_login=False,
                    is_superuser=False,
                    attributes=jsonable(
                        {
                            "database_authorities": a,
                            "members": sorted(
                                f"{text(e['grantee'])} ({GRANTEE_TYPES.get(text(e['granteetype']) or '', '?')})"
                                for e in edges
                                if text(e["rolename"]) == name
                            ),
                            "created": r["create_time"],
                            "remarks": r["remarks"],
                        }
                    ),
                    member_of=sorted(
                        {
                            text(e["rolename"]) or ""
                            for e in edges
                            if text(e["grantee"]) == name and text(e["granteetype"]) == "R"
                        }
                    ),
                    is_system=name.startswith("SYSROLE"),
                    admin_reasons=[ADMIN_AUTHORITIES[x] for x in a if x in ADMIN_AUTHORITIES],
                )
            )
        return out

    async def get_privileges(
        self, database: str | None = None, grantee: str | None = None, limit: int = 2000
    ) -> list[Privilege]:
        out: list[Privilege] = []
        for (g, _), auths in (await self._db_authorities()).items():
            for a in auths:
                out.append(
                    Privilege(grantee=g, object_type="database", object_name=database or "DATABASE", privilege=a)
                )
        for e in await self._role_edges():
            out.append(
                Privilege(
                    grantee=text(e["grantee"]) or "",
                    object_type="role",
                    object_name=text(e["rolename"]) or "",
                    privilege="MEMBER",
                    grantor=text(e["grantor"]),
                    grantable=text(e["admin"]) == "Y",
                )
            )
        schema_rows = await self._try(Q.SCHEMAAUTH_115)
        if schema_rows is None:  # before 11.5 the DML/admin columns do not exist
            schema_rows = await self._try(Q.SCHEMAAUTH) or []
        schema_cols = (
            ("alterinauth", "ALTERIN"),
            ("createinauth", "CREATEIN"),
            ("dropinauth", "DROPIN"),
            ("selectinauth", "SELECTIN"),
            ("insertinauth", "INSERTIN"),
            ("updateinauth", "UPDATEIN"),
            ("deleteinauth", "DELETEIN"),
            ("executeinauth", "EXECUTEIN"),
            ("schemaadmauth", "SCHEMAADM"),
            ("accessctrlauth", "ACCESSCTRL"),
            ("dataaccessauth", "DATAACCESS"),
            ("loadauth", "LOAD"),
        )
        for r in schema_rows:
            for col, p in schema_cols:
                if col in r and text(r[col]) in ("Y", "G"):
                    out.append(
                        Privilege(
                            grantee=text(r["grantee"]) or "",
                            object_type="schema",
                            object_name=text(r["schemaname"]) or "",
                            privilege=p,
                            grantor=text(r["grantor"]),
                            grantable=text(r[col]) == "G",
                        )
                    )
        for r in await self._query(Q.TABAUTH.format(limit=int(limit))):
            for col, p in TAB_PRIVS.items():
                if text(r[col]) in ("Y", "G"):
                    out.append(
                        Privilege(
                            grantee=text(r["grantee"]) or "",
                            object_type="table",
                            object_name=f"{text(r['tabschema'])}.{text(r['tabname'])}",
                            privilege=p,
                            grantor=text(r["grantor"]),
                            grantable=text(r[col]) == "G",
                            database=database,
                        )
                    )
        if grantee:
            out = [p for p in out if p.grantee.upper() == grantee.upper()]
        return out[:limit]
