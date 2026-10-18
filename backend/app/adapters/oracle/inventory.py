"""Oracle inventory. In DB Manager terms a *database* is a container: the PDBs of a CDB (or
the single non-CDB database), and a *schema* is an Oracle user that owns objects.

From ``CDB$ROOT`` a common user sees every open PDB through the ``CDB_*`` views; connected
to a PDB service, the inventory covers that PDB only (``DBA_*`` views)."""

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
from app.adapters.dbapi import jsonable, num, text, utc
from app.adapters.oracle import sql as Q
from app.adapters.oracle.base import OracleBase

TYPES = {
    "table": "TABLE",
    "view": "VIEW",
    "materialized_view": "MATERIALIZED VIEW",
    "sequence": "SEQUENCE",
    "procedure": "PROCEDURE",
    "function": "FUNCTION",
    "package": "PACKAGE",
    "trigger": "TRIGGER",
    "synonym": "SYNONYM",
    "type": "TYPE",
}
KIND_OF = {v: k for k, v in TYPES.items()}
#: DBMS_METADATA object type names
METADATA_TYPES = {
    "table": "TABLE",
    "view": "VIEW",
    "materialized_view": "MATERIALIZED_VIEW",
    "index": "INDEX",
    "sequence": "SEQUENCE",
    "procedure": "PROCEDURE",
    "function": "FUNCTION",
    "package": "PACKAGE",
    "trigger": "TRIGGER",
    "synonym": "SYNONYM",
    "type": "TYPE",
}
ADMIN_SYS_PRIVS = {
    "GRANT ANY PRIVILEGE",
    "GRANT ANY ROLE",
    "GRANT ANY OBJECT PRIVILEGE",
    "ALTER USER",
    "CREATE USER",
    "DROP USER",
    "BECOME USER",
    "ALTER SYSTEM",
    "ALTER DATABASE",
    "CREATE ANY PROCEDURE",
    "EXECUTE ANY PROCEDURE",
    "SELECT ANY TABLE",
    "UPDATE ANY TABLE",
    "DELETE ANY TABLE",
    "DROP ANY TABLE",
    "EXEMPT ACCESS POLICY",
    "EXEMPT REDACTION POLICY",
    "AUDIT SYSTEM",
    "SELECT ANY DICTIONARY",
}
ADMIN_ROLES = {
    "DBA": "DBA role",
    "IMP_FULL_DATABASE": "IMP_FULL_DATABASE",
    "DATAPUMP_IMP_FULL_DATABASE": "DATAPUMP_IMP_FULL_DATABASE",
}
#: SYSDBA-class administrative privileges from the password file
ADMIN_PRIVILEGES = ("sysdba", "sysoper", "sysasm", "sysbackup", "sysdg", "syskm")


class OracleInventoryMixin(OracleBase):
    # ---- container scope ----------------------------------------------------------------------
    async def _containers(self) -> dict[str, int]:
        async def load() -> dict[str, int]:
            rows = await self._try(Q.PDBS) or []
            return {text(r["name"]) or "": int(r["con_id"]) for r in rows}

        return await self._memoized("containers", load)

    async def _scope(self, database: str | None) -> tuple[dict[str, str], dict[str, Any]]:
        """SQL fragments + binds to read ``database``: DBA_ views for the current container,
        CDB_ views filtered by CON_ID when connected to CDB$ROOT and reading a PDB."""
        info = await self._server()
        local = {
            "v": "dba",
            **{k: "" for k in ("con_u", "con_o", "con_s", "con_s_o", "con_tr_o", "con_t_o", "con_c_i", "con_i")},
        }
        if not database or database in (info["con_name"], text(info.get("db_name"))) or not info["in_root"]:
            if database and info["is_cdb"] and not info["in_root"] and database != info["con_name"]:
                raise AdapterNotSupported(
                    Capability.DATABASES,
                    f"connected to PDB {info['con_name']}; register the {database} service to inventory it",
                )
            return local, {}
        con_id = (await self._containers()).get(database)
        if con_id is None:
            raise AdapterNotSupported(Capability.DATABASES, f"container {database!r} not found or not open")
        frag = {
            "v": "cdb",
            "con_u": "AND u.con_id = :con_id",
            "con_o": "AND o.con_id = :con_id",
            "con_s": "AND s.con_id = :con_id",
            "con_s_o": "AND s.con_id = o.con_id",
            "con_tr_o": "AND tr.con_id = o.con_id",
            "con_t_o": "AND t.con_id = o.con_id",
            "con_c_i": "AND c.con_id = i.con_id",
            "con_i": "AND i.con_id = :con_id",
        }
        return frag, {"con_id": con_id}

    async def _maintained_users(self, database: str | None = None) -> set[str]:
        frag, binds = await self._scope(database)
        rows = await self._query(Q.USERS_FOR_SCHEMAS.format(**frag), binds or None)
        return {text(r["username"]) or "" for r in rows if text(r["oracle_maintained"]) == "Y"}

    # ---- databases ----------------------------------------------------------------------------
    async def list_databases(self) -> list[DatabaseInfo]:
        info = await self._server()
        if not info["is_cdb"]:
            size = await self._try_one(Q.DATAFILE_BYTES)
            return [
                DatabaseInfo(
                    name=text(info.get("db_name")) or info["con_name"] or "",
                    size_bytes=int(num(size["bytes"], 0) or 0) if size and size["bytes"] is not None else None,
                    allow_connections=True,
                    accessible=True,
                    extra={
                        "type": "non-CDB",
                        "open_mode": text(info.get("open_mode")),
                        "log_mode": text(info.get("log_mode")),
                    },
                )
            ]
        out = []
        sizes: dict[int, int] = {}
        if info["in_root"]:
            for r in await self._try(Q.CDB_DATAFILE_BYTES) or []:
                sizes[int(r["con_id"])] = int(num(r["bytes"], 0) or 0)
            out.append(
                DatabaseInfo(
                    name="CDB$ROOT",
                    is_system=True,
                    size_bytes=sizes.get(1),
                    allow_connections=True,
                    accessible=True,
                    extra={"type": "CDB root", "con_id": 1, "open_mode": text(info.get("open_mode"))},
                )
            )
        for r in await self._try(Q.PDBS) or []:
            name = text(r["name"]) or ""
            open_mode = text(r["open_mode"]) or ""
            out.append(
                DatabaseInfo(
                    name=name,
                    size_bytes=int(num(r["total_size"], 0) or 0) or sizes.get(int(r["con_id"])),
                    is_system=name == "PDB$SEED",
                    is_template=name == "PDB$SEED",
                    allow_connections=open_mode.startswith("READ") and text(r["restricted"]) != "YES",
                    accessible=open_mode.startswith("READ") and (info["in_root"] or name == info["con_name"]),
                    extra=jsonable(
                        {
                            "type": "PDB",
                            "con_id": r["con_id"],
                            "open_mode": open_mode,
                            "restricted": text(r["restricted"]) == "YES",
                            "open_time": r["open_time"],
                        }
                    ),
                )
            )
        if not out:  # PDB without V$ access
            out.append(DatabaseInfo(name=info["con_name"] or "", extra={"type": "PDB"}))
        return out

    # ---- schemas & objects ----------------------------------------------------------------------
    async def list_schemas(self, database: str) -> list[SchemaInfo]:
        frag, binds = await self._scope(database)
        users = await self._query(Q.USERS_FOR_SCHEMAS.format(**frag), binds or None)
        counts: dict[str, dict[str, int]] = {}
        for r in await self._query(Q.OBJECT_COUNTS.format(**frag), binds or None):
            kind = KIND_OF.get(text(r["object_type"]) or "", (text(r["object_type"]) or "").lower())
            counts.setdefault(text(r["owner"]) or "", {})[f"{kind}s"] = int(r["n"] or 0)
        sizes = {
            text(r["owner"]) or "": int(num(r["bytes"], 0) or 0)
            for r in await self._try(Q.SCHEMA_SIZES.format(**frag), binds or None) or []
        }
        out = []
        for u in users:
            name = text(u["username"]) or ""
            if name not in counts and text(u["oracle_maintained"]) == "Y":
                continue
            out.append(
                SchemaInfo(
                    name=name,
                    owner=name,
                    is_system=text(u["oracle_maintained"]) == "Y",
                    object_counts=counts.get(name, {}),
                    size_bytes=sizes.get(name),
                )
            )
        return out

    async def list_objects(self, database: str, schema: str, object_type: str | None = None) -> list[ObjectInfo]:
        if object_type and object_type not in TYPES and object_type not in ("index", "constraint"):
            raise AdapterNotSupported(Capability.OBJECTS, f"object type {object_type!r} is not listed for Oracle")
        frag, binds = await self._scope(database)
        binds = {**binds, "owner": schema}
        out: list[ObjectInfo] = []
        types = [TYPES[object_type]] if object_type in TYPES else ([] if object_type else list(TYPES.values()))
        if types:
            sql = Q.OBJECTS.format(types=", ".join(f"'{t}'" for t in types), **frag)
            for r in await self._query(sql, binds):
                kind = KIND_OF.get(text(r["object_type"]) or "", (text(r["object_type"]) or "").lower())
                out.append(
                    ObjectInfo(
                        name=text(r["object_name"]) or "",
                        schema_name=schema,
                        type=kind,
                        owner=schema,
                        parent=text(r["trigger_table"]) if kind == "trigger" else None,
                        size_bytes=int(num(r["bytes"], 0) or 0) if kind == "table" and r["bytes"] is not None else None,
                        row_estimate=int(r["num_rows"]) if kind == "table" and r["num_rows"] is not None else None,
                        extra=jsonable(
                            {
                                "status": r["status"],
                                "created": r["created"],
                                "last_ddl": r["last_ddl_time"],
                                "last_analyzed": r["last_analyzed"],
                                "partitioned": text(r["partitioned"]) == "YES" if r["partitioned"] else None,
                                "temporary": text(r["temporary"]) == "Y" if r["temporary"] else None,
                                "iot": r["iot_type"],
                                "system_generated": text(r["generated"]) == "Y",
                            }
                        ),
                    )
                )
        if object_type in (None, "index"):
            for r in await self._query(Q.INDEXES.format(table_filter="", **frag), binds):
                out.append(
                    ObjectInfo(
                        name=text(r["index_name"]) or "",
                        schema_name=schema,
                        type="index",
                        owner=schema,
                        parent=text(r["table_name"]),
                        row_estimate=int(r["num_rows"]) if r["num_rows"] is not None else None,
                        extra=jsonable(
                            {
                                "index_type": r["index_type"],
                                "unique": text(r["uniqueness"]) == "UNIQUE",
                                "status": r["status"],
                                "visible": text(r["visibility"]) != "INVISIBLE",
                                "partitioned": text(r["partitioned"]) == "YES",
                                "columns": r["columns"],
                                "last_analyzed": r["last_analyzed"],
                            }
                        ),
                    )
                )
        return out

    async def get_object_detail(self, database: str, schema: str, name: str, object_type: str) -> ObjectDetail:
        info = await self._server()
        if database and info["in_root"] and database not in ("CDB$ROOT",):
            raise AdapterNotSupported(
                Capability.OBJECT_DDL, "object details are read in the current container; register the PDB service"
            )
        await self._scope(database)  # rejects foreign PDBs when connected to a PDB
        binds = {"owner": schema, "name": name}
        row = await self._one(Q.OBJECT_ROW, binds)
        if row is None:
            raise AdapterNotSupported(Capability.OBJECT_DDL, f"{schema}.{name} not found or not visible")
        cols = []
        for c in await self._query(Q.COLUMNS, binds):
            t = text(c["data_type"]) or ""
            if t in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"):
                t += f"({int(c['char_length'] or 0)}{' CHAR' if text(c['char_used']) == 'C' and t in ('VARCHAR2', 'CHAR') else ''})"
            elif t == "NUMBER" and c["data_precision"] is not None:
                t += f"({int(c['data_precision'])}{',' + str(int(c['data_scale'])) if c['data_scale'] else ''})"
            elif t == "RAW":
                t += f"({int(c['data_length'] or 0)})"
            cols.append(
                ColumnInfo(
                    name=text(c["column_name"]) or "",
                    data_type=t,
                    nullable=text(c["nullable"]) == "Y",
                    default=(text(c["data_default"]) or None),
                    position=int(c["column_id"] or 0),
                )
            )
        kind = KIND_OF.get(text(row["object_type"]) or "", object_type)
        idx = (
            [
                jsonable(dict(r))
                for r in await self._query(
                    Q.INDEXES.format(v="dba", table_filter="AND i.table_name = :name", con_c_i="", con_i=""), binds
                )
            ]
            if kind == "table"
            else []
        )
        cons = [jsonable(dict(r)) for r in await self._query(Q.CONSTRAINTS, binds)] if kind == "table" else []
        trg = [jsonable(dict(r)) for r in await self._query(Q.TRIGGERS_OF, binds)] if kind in ("table", "view") else []
        stats = await self._try_one(Q.TABLE_STATS, binds) if kind == "table" else None
        definition, note = None, None
        mtype = METADATA_TYPES.get(kind)
        if mtype:
            ddl = await self._try_one(Q.GET_DDL, {"otype": mtype, "name": name, "owner": schema})
            definition = text(ddl["ddl"]) if ddl else None
            if definition is None:
                note = (
                    "DBMS_METADATA.GET_DDL returned nothing: objects of other schemas need SELECT_CATALOG_ROLE "
                    "(granted directly or as a default role)"
                )
            else:
                definition = definition.strip()
        return ObjectDetail(
            object=ObjectInfo(
                name=name, schema_name=schema, type=kind, owner=schema, extra={"status": text(row["status"])}
            ),
            columns=cols,
            indexes=idx,
            constraints=cons,
            triggers=trg,
            statistics=jsonable(dict(stats)) if stats else {},
            definition=definition,
            definition_note=note,
        )

    # ---- principals ------------------------------------------------------------------------------
    async def _principal_maps(
        self,
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
        roles: dict[str, list[dict[str, Any]]] = {}
        for r in await self._query(Q.ROLE_PRIVS):
            roles.setdefault(text(r["grantee"]) or "", []).append(
                {
                    "role": text(r["granted_role"]),
                    "admin": text(r["admin_option"]) == "YES",
                    "default": text(r["default_role"]) == "YES",
                }
            )
        sysp: dict[str, list[dict[str, Any]]] = {}
        for r in await self._query(Q.SYS_PRIVS):
            sysp.setdefault(text(r["grantee"]) or "", []).append(
                {"privilege": text(r["privilege"]), "admin": text(r["admin_option"]) == "YES"}
            )
        pw: dict[str, dict[str, Any]] = {}
        for r in await self._try(Q.PWFILE_USERS) or []:
            pw[text(r["username"]) or ""] = {k: text(r[k]) == "TRUE" for k in ADMIN_PRIVILEGES}
        return roles, sysp, pw

    def _admin_reasons(
        self, name: str, roles: list[dict[str, Any]], sysp: list[dict[str, Any]], pw: dict[str, Any]
    ) -> list[str]:
        reasons = [k.upper() + " (password file)" for k, v in (pw or {}).items() if v]
        reasons += [ADMIN_ROLES[r["role"]] for r in roles if r["role"] in ADMIN_ROLES]
        reasons += sorted({p["privilege"] for p in sysp if p["privilege"] in ADMIN_SYS_PRIVS})
        reasons += sorted({f"{p['privilege']} WITH ADMIN OPTION" for p in sysp if p["admin"]} - set(reasons))[:5]
        return reasons

    async def list_users(self) -> list[DbUser]:
        roles, sysp, pw = await self._principal_maps()
        out = []
        for r in await self._query(Q.USERS):
            name = text(r["username"]) or ""
            status = text(r["account_status"]) or ""
            ur, us = roles.get(name, []), sysp.get(name, [])
            reasons = self._admin_reasons(name, ur, us, pw.get(name, {}))
            out.append(
                DbUser(
                    name=name,
                    can_login=status == "OPEN" or status.startswith("EXPIRED(GRACE)"),
                    is_superuser=name in ("SYS",)
                    or bool(pw.get(name, {}).get("sysdba"))
                    or any(x["role"] == "DBA" for x in ur),
                    attributes=jsonable(
                        {
                            "account_status": status,
                            "lock_date": r["lock_date"],
                            "expiry_date": r["expiry_date"],
                            "default_tablespace": r["default_tablespace"],
                            "temporary_tablespace": r["temporary_tablespace"],
                            "profile": r["profile"],
                            "created": r["created"],
                            "authentication_type": r["authentication_type"],
                            "common": text(r["common"]) == "YES",
                            "password_versions": r["password_versions"],
                            "system_privileges": sorted({p["privilege"] for p in us}),
                            "administrative_privileges": [k.upper() for k, v in pw.get(name, {}).items() if v],
                        }
                    ),
                    member_of=sorted({x["role"] for x in ur}),
                    valid_until=utc(r["expiry_date"]),
                    last_login_at=utc(r["last_login"]),
                    is_system=text(r["oracle_maintained"]) == "Y",
                    admin_reasons=reasons,
                )
            )
        return out

    async def list_roles(self) -> list[DbUser]:
        roles, sysp, _ = await self._principal_maps()
        members: dict[str, list[str]] = {}
        for grantee, rs in roles.items():
            for x in rs:
                members.setdefault(x["role"], []).append(grantee)
        out = []
        for r in await self._query(Q.ROLES):
            name = text(r["role"]) or ""
            out.append(
                DbUser(
                    name=name,
                    can_login=False,
                    is_superuser=name == "DBA",
                    attributes=jsonable(
                        {
                            "authentication_type": r["authentication_type"],
                            "common": text(r["common"]) == "YES",
                            "members": sorted(members.get(name, []))[:200],
                            "system_privileges": sorted({p["privilege"] for p in sysp.get(name, [])}),
                        }
                    ),
                    member_of=sorted({x["role"] for x in roles.get(name, [])}),
                    is_system=text(r["oracle_maintained"]) == "Y",
                    admin_reasons=self._admin_reasons(name, roles.get(name, []), sysp.get(name, []), {}),
                )
            )
        return out

    async def get_privileges(
        self, database: str | None = None, grantee: str | None = None, limit: int = 2000
    ) -> list[Privilege]:
        out: list[Privilege] = []
        # object grants first: they are what users look for, and the system-privilege lists of
        # built-in roles are long enough to crowd them out of a limited result
        for r in await self._query(Q.TAB_PRIVS, {"lim": int(limit)}):
            otype = (text(r["type"]) or "TABLE").lower().replace(" ", "_")
            out.append(
                Privilege(
                    grantee=text(r["grantee"]) or "",
                    object_type=otype,
                    object_name=f"{text(r['owner'])}.{text(r['table_name'])}",
                    privilege=text(r["privilege"]) or "",
                    grantor=text(r["grantor"]),
                    grantable=text(r["grantable"]) == "YES",
                    database=database,
                )
            )
        for r in await self._try(Q.COL_PRIVS, {"lim": int(limit)}) or []:
            out.append(
                Privilege(
                    grantee=text(r["grantee"]) or "",
                    object_type="column",
                    object_name=f"{text(r['owner'])}.{text(r['table_name'])}.{text(r['column_name'])}",
                    privilege=text(r["privilege"]) or "",
                    grantor=text(r["grantor"]),
                    grantable=text(r["grantable"]) == "YES",
                    database=database,
                )
            )
        roles, sysp, pw = await self._principal_maps()
        for g, rs in roles.items():
            for x in rs:
                out.append(
                    Privilege(
                        grantee=g,
                        object_type="role",
                        object_name=x["role"] or "",
                        privilege="MEMBER",
                        grantable=x["admin"],
                    )
                )
        for g, ps in sysp.items():
            for p in ps:
                out.append(
                    Privilege(
                        grantee=g,
                        object_type="system",
                        object_name="DATABASE",
                        privilege=p["privilege"] or "",
                        grantable=p["admin"],
                    )
                )
        for g, flags in pw.items():
            for k, v in flags.items():
                if v:
                    out.append(Privilege(grantee=g, object_type="system", object_name="INSTANCE", privilege=k.upper()))
        if grantee:
            out = [p for p in out if p.grantee.upper() == grantee.upper()]
        return out[:limit]
