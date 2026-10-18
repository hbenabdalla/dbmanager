"""SQL Server inventory: databases, schemas, objects, logins, users, roles, permissions."""

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
from app.adapters.mssql import sql as Q
from app.adapters.mssql.base import SQLServerBase, qn

SYSTEM_DBS = {"master", "model", "msdb", "tempdb", "distribution", "SSISDB", "ReportServer", "ReportServerTempDB"}
SYSTEM_SCHEMAS = {"sys", "INFORMATION_SCHEMA", "guest"}
FIXED_SERVER_ADMIN = {
    "sysadmin": "sysadmin (unrestricted)",
    "securityadmin": "securityadmin (can grant itself sysadmin-equivalent rights)",
    "serveradmin": "serveradmin",
    "setupadmin": "setupadmin (linked servers)",
    "processadmin": "processadmin (kills sessions)",
    "dbcreator": "dbcreator",
    "bulkadmin": "bulkadmin (reads files via BULK INSERT)",
    "##MS_ServerSecurityStateReader##": None,
}
ADMIN_PERMISSIONS = {
    "CONTROL SERVER",
    "ALTER ANY LOGIN",
    "ALTER ANY SERVER ROLE",
    "IMPERSONATE ANY LOGIN",
    "ALTER SERVER STATE",
    "ALTER ANY CREDENTIAL",
    "ALTER SETTINGS",
    "UNSAFE ASSEMBLY",
    "ALTER ANY LINKED SERVER",
}
TYPES = {
    "table": "'U'",
    "view": "'V'",
    "procedure": "'P', 'PC'",
    "function": "'FN', 'IF', 'TF', 'FS', 'FT'",
    "trigger": "'TR'",
    "sequence": "'SO'",
    "synonym": "'SN'",
}
KIND_OF = {
    "U": "table",
    "V": "view",
    "P": "procedure",
    "PC": "procedure",
    "TR": "trigger",
    "SO": "sequence",
    "SN": "synonym",
}
LOGIN_TYPES = {
    "S": "SQL login",
    "U": "Windows login",
    "G": "Windows group",
    "E": "Entra ID user",
    "X": "Entra ID group",
    "C": "certificate",
    "K": "asymmetric key",
}
SYSTEM_LOGINS = {
    "##MS_PolicyEventProcessingLogin##",
    "##MS_PolicyTsqlExecutionLogin##",
    "##MS_AgentSigningCertificate##",
    "NT AUTHORITY\\SYSTEM",
    "NT SERVICE\\MSSQLSERVER",
    "NT SERVICE\\SQLSERVERAGENT",
    "NT SERVICE\\SQLWriter",
    "NT SERVICE\\Winmgmt",
}


def _kind(t: str) -> str:
    t = (t or "").strip()
    if t in ("FN", "IF", "TF", "FS", "FT"):
        return "function"
    return KIND_OF.get(t, t.lower())


class SQLServerInventoryMixin(SQLServerBase):
    async def list_databases(self) -> list[DatabaseInfo]:
        qs = "d.is_query_store_on," if await self._version() >= 13 else ""
        rows = await self._query(Q.DATABASES.format(query_store=qs))
        sizes: dict[tuple[int, str], int] = {}
        for f in await self._try(Q.DATABASE_FILES) or []:
            sizes[(int(f["database_id"]), text(f["type_desc"]) or "")] = int(num(f["bytes"], 0) or 0)
        out = []
        for r in rows:
            name = text(r["name"]) or ""
            did = int(r["database_id"])
            data, log = sizes.get((did, "ROWS")), sizes.get((did, "LOG"))
            out.append(
                DatabaseInfo(
                    name=name,
                    owner=text(r["owner_name"]),
                    size_bytes=(data or 0) + (log or 0) if data is not None else None,
                    collation=text(r["collation_name"]),
                    allow_connections=text(r["state_desc"]) == "ONLINE"
                    and text(r["user_access_desc"]) != "SINGLE_USER",
                    is_system=name in SYSTEM_DBS or did <= 4,
                    accessible=bool(r["accessible"]) and text(r["state_desc"]) == "ONLINE",
                    extra=jsonable(
                        {
                            "state": r["state_desc"],
                            "recovery_model": r["recovery_model_desc"],
                            "compatibility_level": r["compatibility_level"],
                            "data_bytes": data,
                            "log_bytes": log,
                            "read_only": bool(r["is_read_only"]),
                            "user_access": r["user_access_desc"],
                            "created": r["create_date"],
                            "encrypted_tde": bool(r["is_encrypted"]),
                            "trustworthy": bool(r["is_trustworthy_on"]),
                            "auto_shrink": bool(r["is_auto_shrink_on"]),
                            "auto_close": bool(r["is_auto_close_on"]),
                            "log_reuse_wait": r["log_reuse_wait_desc"],
                            "read_committed_snapshot": bool(r["is_read_committed_snapshot_on"]),
                            "snapshot_isolation": r["snapshot_isolation_state_desc"],
                            "query_store": bool(r.get("is_query_store_on")) if "is_query_store_on" in r else None,
                        }
                    ),
                )
            )
        return out

    async def list_schemas(self, database: str) -> list[SchemaInfo]:
        out = []
        for r in await self._query(Q.SCHEMAS, database=database):
            name = text(r["name"]) or ""
            out.append(
                SchemaInfo(
                    name=name,
                    owner=text(r["owner_name"]),
                    is_system=name in SYSTEM_SCHEMAS or int(r["schema_id"]) >= 16384,
                    object_counts={
                        k: int(r[k] or 0)
                        for k in ("tables", "views", "procedures", "functions", "triggers", "sequences", "synonyms")
                    },
                )
            )
        return out

    async def list_objects(self, database: str, schema: str, object_type: str | None = None) -> list[ObjectInfo]:
        if object_type and object_type not in TYPES and object_type not in ("index", "constraint"):
            raise AdapterNotSupported(Capability.OBJECTS, f"object type {object_type!r} does not exist in SQL Server")
        out: list[ObjectInfo] = []
        types = [TYPES[object_type]] if object_type in TYPES else ([] if object_type else list(TYPES.values()))
        if types:
            sizes = {int(r["object_id"]): r for r in await self._try(Q.TABLE_SIZES, [schema], database=database) or []}
            for r in await self._query(Q.OBJECTS.format(types=", ".join(types)), [schema], database=database):
                kind = _kind(text(r["type"]) or "")
                sz = sizes.get(int(r["object_id"]))
                out.append(
                    ObjectInfo(
                        name=text(r["name"]) or "",
                        schema_name=schema,
                        type=kind,
                        parent=text(r["parent_name"]) if kind == "trigger" else None,
                        size_bytes=int(sz["used_bytes"]) if sz and kind == "table" else None,
                        row_estimate=int(sz["row_count"]) if sz and kind == "table" else None,
                        extra=jsonable(
                            {"type_desc": r["type_desc"], "created": r["create_date"], "modified": r["modify_date"]}
                        ),
                    )
                )
        if object_type in (None, "index"):
            for r in await self._query(Q.INDEXES.format(table_filter=""), [schema], database=database):
                out.append(
                    ObjectInfo(
                        name=text(r["name"]) or "",
                        schema_name=schema,
                        type="index",
                        parent=text(r["table_name"]),
                        extra=jsonable(
                            {
                                "index_type": r["type_desc"],
                                "unique": bool(r["is_unique"]),
                                "primary": bool(r["is_primary_key"]),
                                "disabled": bool(r["is_disabled"]),
                                "filtered": bool(r["has_filter"]),
                                "columns": r["key_columns"],
                                "included": r["included_columns"],
                            }
                        ),
                    )
                )
        return out

    async def get_object_detail(self, database: str, schema: str, name: str, object_type: str) -> ObjectDetail:
        full = f"{qn(schema)}.{qn(name)}"
        cols = []
        for c in await self._query(Q.COLUMNS, [full], database=database):
            t = text(c["type_name"]) or ""
            if t in ("varchar", "char", "varbinary", "binary"):
                t += f"({'max' if c['max_length'] == -1 else c['max_length']})"
            elif t in ("nvarchar", "nchar"):
                t += f"({'max' if c['max_length'] == -1 else int(c['max_length']) // 2})"
            elif t in ("decimal", "numeric"):
                t += f"({c['precision']},{c['scale']})"
            cols.append(
                ColumnInfo(
                    name=text(c["name"]) or "",
                    data_type=t,
                    nullable=bool(c["is_nullable"]),
                    default=text(c["default_value"]),
                    position=int(c["column_id"]),
                )
            )
        row = await self._one(Q.OBJECT_ROW, [full], database=database)
        if row is None:
            raise AdapterNotSupported(Capability.OBJECT_DDL, f"{schema}.{name} not found or not visible to this login")
        idx = [
            jsonable(dict(r))
            for r in await self._query(
                Q.INDEXES.format(table_filter="AND o.object_id = OBJECT_ID(?)"), [schema, full], database=database
            )
        ]
        cons = [jsonable(dict(r)) for r in await self._query(Q.CONSTRAINTS, [full, full, full], database=database)]
        trg = [jsonable(dict(r)) for r in await self._query(Q.TRIGGERS_OF, [full], database=database)]
        definition = text(row["definition_text"])
        note = None
        if (text(row["type"]) or "").strip() == "U":
            lines = [
                f"    {qn(c.name)} {c.data_type}{'' if c.nullable else ' NOT NULL'}{(' DEFAULT ' + c.default) if c.default else ''}"
                for c in cols
            ]
            definition = f"CREATE TABLE {full} (\n" + ",\n".join(lines) + "\n);"
            note = "reconstructed from sys.columns (constraints, indexes and options are listed separately)"
        elif definition is None:
            note = "definition hidden: the login needs VIEW DEFINITION (or the module is encrypted)"
        obj = ObjectInfo(name=name, schema_name=schema, type=object_type)
        return ObjectDetail(
            object=obj,
            columns=cols,
            indexes=idx,
            constraints=cons,
            triggers=trg,
            definition=definition,
            definition_note=note,
        )

    # ---- principals ------------------------------------------------------------------------
    async def _logins(self) -> list[DbUser]:
        logins = await self._query(Q.LOGINS)
        members: dict[str, list[str]] = {}
        for m in await self._try(Q.SERVER_ROLE_MEMBERS) or []:
            members.setdefault(text(m["member_name"]) or "", []).append(text(m["role_name"]) or "")
        perms: dict[str, list[str]] = {}
        for p in await self._try(Q.SERVER_PERMISSIONS) or []:
            if (text(p["state_desc"]) or "").startswith("GRANT"):
                perms.setdefault(text(p["grantee"]) or "", []).append(text(p["permission_name"]) or "")
        out = []
        for r in logins:
            name = text(r["name"]) or ""
            roles = sorted(members.get(name, []))
            reasons = [FIXED_SERVER_ADMIN[x] for x in roles if FIXED_SERVER_ADMIN.get(x)]
            reasons += sorted(set(perms.get(name, [])) & ADMIN_PERMISSIONS)
            if text(r["sid_hex"]) == "0x01":
                reasons.insert(0, "built-in sa login")
            out.append(
                DbUser(
                    name=name,
                    can_login=not bool(r["is_disabled"]) and not bool(r["is_locked"] or 0),
                    is_superuser="sysadmin" in roles or "CONTROL SERVER" in perms.get(name, []),
                    attributes=jsonable(
                        {
                            "login_type": LOGIN_TYPES.get(text(r["type"]) or "", text(r["type_desc"])),
                            "disabled": bool(r["is_disabled"]),
                            "locked": bool(r["is_locked"] or 0),
                            "expired": bool(r["is_expired"] or 0),
                            "password_policy": r["is_policy_checked"],
                            "password_expiration": r["is_expiration_checked"],
                            "password_last_set": r["password_last_set"],
                            "default_database": r["default_database_name"],
                            "created": r["create_date"],
                            "server_permissions": sorted(set(perms.get(name, []))),
                            "is_sa": text(r["sid_hex"]) == "0x01",
                        }
                    ),
                    member_of=roles,
                    is_system=name in SYSTEM_LOGINS or name.startswith("##"),
                    admin_reasons=reasons,
                )
            )
        return out

    async def list_users(self) -> list[DbUser]:
        return await self._logins()

    async def list_roles(self) -> list[DbUser]:
        out = []
        for r in await self._query(Q.SERVER_ROLES):
            name = text(r["name"]) or ""
            out.append(
                DbUser(
                    name=name,
                    can_login=False,
                    is_superuser=name == "sysadmin",
                    attributes={"scope": "server", "fixed": bool(r["is_fixed_role"])},
                    is_system=bool(r["is_fixed_role"]) or name == "public",
                    admin_reasons=[FIXED_SERVER_ADMIN[name]] if FIXED_SERVER_ADMIN.get(name) else [],
                )
            )
        db = self.spec.database
        if db:
            members: dict[str, list[str]] = {}
            for m in await self._try(Q.DB_ROLE_MEMBERS, database=db) or []:
                members.setdefault(text(m["role_name"]) or "", []).append(text(m["member_name"]) or "")
            for r in await self._try(Q.DB_PRINCIPALS, database=db) or []:
                if (text(r["type"]) or "") not in ("R", "A"):
                    continue
                name = text(r["name"]) or ""
                out.append(
                    DbUser(
                        name=f"{db}:{name}",
                        can_login=False,
                        attributes={
                            "scope": "database",
                            "database": db,
                            "fixed": bool(r["is_fixed_role"]),
                            "members": members.get(name, []),
                        },
                        is_system=bool(r["is_fixed_role"]) or name == "public",
                        admin_reasons=["db_owner (full control of the database)"] if name == "db_owner" else [],
                    )
                )
        return out

    async def database_users(self, database: str) -> list[dict[str, Any]]:
        members: dict[str, list[str]] = {}
        for m in await self._try(Q.DB_ROLE_MEMBERS, database=database) or []:
            members.setdefault(text(m["member_name"]) or "", []).append(text(m["role_name"]) or "")
        return [
            jsonable({**dict(r), "roles": members.get(text(r["name"]) or "", [])})
            for r in await self._query(Q.DB_PRINCIPALS, database=database)
            if (text(r["type"]) or "") not in ("R", "A")
        ]

    async def get_privileges(
        self, database: str | None = None, grantee: str | None = None, limit: int = 2000
    ) -> list[Privilege]:
        out: list[Privilege] = []
        if database is None:
            for r in await self._query(Q.SERVER_PERMISSIONS):
                out.append(
                    Privilege(
                        grantee=text(r["grantee"]) or "",
                        object_type="server"
                        if text(r["class_desc"]) == "SERVER"
                        else (text(r["class_desc"]) or "").lower(),
                        object_name=text(r["object_name"]) or "SERVER",
                        privilege=f"{'DENY ' if text(r['state_desc']) == 'DENY' else ''}{text(r['permission_name'])}",
                        grantable=text(r["state_desc"]) == "GRANT_WITH_GRANT_OPTION",
                    )
                )
            for m in await self._query(Q.SERVER_ROLE_MEMBERS):
                out.append(
                    Privilege(
                        grantee=text(m["member_name"]) or "",
                        object_type="role",
                        object_name=text(m["role_name"]) or "",
                        privilege="MEMBER",
                    )
                )
        else:
            for r in await self._query(Q.DB_PERMISSIONS, database=database):
                cls = (text(r["class_desc"]) or "").upper()
                otype = {
                    "DATABASE": "database",
                    "SCHEMA": "schema",
                    "OBJECT_OR_COLUMN": "column" if r["column_name"] else "table",
                }.get(cls, cls.lower())
                name = text(r["object_name"]) or database
                if r["column_name"]:
                    name = f"{name}.{text(r['column_name'])}"
                out.append(
                    Privilege(
                        grantee=text(r["grantee"]) or "",
                        object_type=otype,
                        object_name=name,
                        privilege=f"{'DENY ' if text(r['state_desc']) == 'DENY' else ''}{text(r['permission_name'])}",
                        grantable=text(r["state_desc"]) == "GRANT_WITH_GRANT_OPTION",
                        grantor=text(r["grantor"]),
                        database=database,
                    )
                )
            for m in await self._query(Q.DB_ROLE_MEMBERS, database=database):
                out.append(
                    Privilege(
                        grantee=text(m["member_name"]) or "",
                        object_type="role",
                        object_name=text(m["role_name"]) or "",
                        privilege="MEMBER",
                        database=database,
                    )
                )
        if grantee:
            out = [p for p in out if p.grantee.lower() == grantee.lower()]
        return out[:limit]


__all__ = ["SQLServerInventoryMixin", "utc"]
