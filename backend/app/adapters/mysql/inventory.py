"""MySQL-family inventory: databases (= schemas), objects, accounts, roles and grants."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.adapters.base import (
    AdapterError,
    AdapterNotSupported,
    AdapterPermissionError,
    Capability,
    ColumnInfo,
    DatabaseInfo,
    DbUser,
    ObjectDetail,
    ObjectInfo,
    Privilege,
    SchemaInfo,
)
from app.adapters.dbapi import AdapterObjectMissing, jsonable, num, text, utc
from app.adapters.mysql import sql as Q
from app.adapters.mysql.base import MySQLFamilyBase, account, normalize_grantee, qident

SYSTEM_ACCOUNTS = {
    "mysql.sys",
    "mysql.session",
    "mysql.infoschema",
    "debian-sys-maint",
    "mariadb.sys",
    "rdsadmin",
    "rdsrepladmin",
    "azure_superuser",
    "cloudsqladmin",
    "cloudsqlreplica",
    "PUBLIC",
}
#: global (``*.*``) privileges that make an account an administrator, with the reason shown
ADMIN_PRIVS = {
    "Super_priv": "SUPER",
    "Grant_priv": "GRANT OPTION on *.*",
    "Create_user_priv": "CREATE USER",
    "File_priv": "FILE (read/write server files)",
    "Shutdown_priv": "SHUTDOWN",
    "Reload_priv": "RELOAD",
}
#: sensitive but not administrative (reported as attributes, not as admin reasons)
SENSITIVE_PRIVS = {
    "Process_priv": "PROCESS (sees all sessions and statements)",
    "Repl_slave_priv": "REPLICATION SLAVE (reads the binary log)",
}
POWERFUL_DYNAMIC = {
    "SYSTEM_USER",
    "ROLE_ADMIN",
    "CONNECTION_ADMIN",
    "SYSTEM_VARIABLES_ADMIN",
    "ENCRYPTION_KEY_ADMIN",
    "AUDIT_ADMIN",
    "BINLOG_ADMIN",
    "SET_USER_ID",
    "PERSIST_RO_VARIABLES_ADMIN",
    "SET_ANY_DEFINER",
}
OBJECT_TYPES = {
    "table": "'BASE TABLE', 'SYSTEM VERSIONED'",
    "view": "'VIEW'",
    "sequence": "'SEQUENCE'",
}


class MySQLInventoryMixin(MySQLFamilyBase):
    # ---- flavor hooks (accounts) -------------------------------------------------------------
    async def _account_rows(self) -> list[dict[str, Any]] | None:
        """Rows: user, host, is_role, locked, password_expired, plugin, has_password,
        password_last_changed, password_lifetime, max_user_connections and *_priv columns.
        ``None`` when the account may not read the grant tables."""
        raise NotImplementedError

    async def _role_edges(self) -> list[tuple[str, str]] | None:
        """(member account, role name) pairs."""
        raise NotImplementedError

    async def _dynamic_grants(self) -> dict[str, list[str]]:
        return {}

    # ---- databases / schemas -----------------------------------------------------------------
    async def list_databases(self) -> list[DatabaseInfo]:
        out = []
        for r in await self._query(Q.LIST_DATABASES):
            name = text(r["name"]) or ""
            out.append(
                DatabaseInfo(
                    name=name,
                    size_bytes=int(num(r["size_bytes"], 0) or 0),
                    encoding=text(r["charset"]),
                    collation=text(r["collation_name"]),
                    is_system=name.lower() in Q.SYSTEM_SCHEMAS,
                    extra={"tables": int(r["tables"] or 0)},
                )
            )
        return out

    async def list_schemas(self, database: str) -> list[SchemaInfo]:
        """In MySQL/MariaDB a database *is* a schema: one schema per database."""
        r = await self._one(Q.SCHEMA_COUNTS, [database] * 7)
        counts = (
            {k: int(r[k] or 0) for k in ("tables", "views", "indexes", "procedures", "functions", "triggers")}
            if r
            else {}
        )
        return [
            SchemaInfo(
                name=database,
                is_system=database.lower() in Q.SYSTEM_SCHEMAS,
                object_counts=counts,
                size_bytes=int(num(r["size_bytes"], 0) or 0) if r else None,
            )
        ]

    async def list_objects(self, database: str, schema: str, object_type: str | None = None) -> list[ObjectInfo]:
        db = schema or database
        kinds = [object_type] if object_type else ["table", "view", "index", "procedure", "function", "trigger"]
        out: list[ObjectInfo] = []
        for kind in kinds:
            if kind in OBJECT_TYPES:
                for r in await self._query(Q.TABLES.format(types=OBJECT_TYPES[kind]), [db]):
                    data, idx = int(r["data_bytes"] or 0), int(r["index_bytes"] or 0)
                    out.append(
                        ObjectInfo(
                            name=text(r["name"]) or "",
                            schema_name=db,
                            type=kind,
                            size_bytes=data + idx if kind == "table" else None,
                            row_estimate=int(r["row_estimate"]) if r["row_estimate"] is not None else None,
                            extra=jsonable(
                                {
                                    "engine": r["engine"],
                                    "data_bytes": data,
                                    "index_bytes": idx,
                                    "free_bytes": r["free_bytes"],
                                    "row_format": r["row_format"],
                                    "created": r["created"],
                                    "updated": r["updated"],
                                    "comment": r["comment_text"] or None,
                                    "system_versioned": r["table_type"] == "SYSTEM VERSIONED",
                                }
                            ),
                        )
                    )
            elif kind == "index":
                sizes: dict[tuple[str, str], int] = {}
                for s in await self._try(Q.INDEX_SIZES, [db]) or []:
                    sizes[(text(s["table_name"]) or "", text(s["index_name"]) or "")] = int(
                        num(s["size_bytes"], 0) or 0
                    )
                for r in await self._query(Q.INDEXES, [db]):
                    t, n = text(r["table_name"]) or "", text(r["name"]) or ""
                    out.append(
                        ObjectInfo(
                            name=n,
                            schema_name=db,
                            type="index",
                            parent=t,
                            size_bytes=sizes.get((t, n)),
                            extra={
                                "unique": not int(r["non_unique"] or 0),
                                "primary": n == "PRIMARY",
                                "index_type": text(r["index_type"]),
                                "columns": text(r["columns_list"]),
                                "cardinality": r["cardinality"],
                            },
                        )
                    )
            elif kind in ("procedure", "function"):
                for r in await self._query(Q.ROUTINES, [db, kind.upper()]):
                    out.append(
                        ObjectInfo(
                            name=text(r["name"]) or "",
                            schema_name=db,
                            type=kind,
                            owner=text(r["definer"]),
                            extra=jsonable(
                                {
                                    "returns": r["returns_type"],
                                    "security_type": r["security_type"],
                                    "deterministic": r["is_deterministic"],
                                    "created": r["created"],
                                    "altered": r["altered"],
                                }
                            ),
                        )
                    )
            elif kind == "trigger":
                for r in await self._query(Q.TRIGGERS, [db]):
                    out.append(
                        ObjectInfo(
                            name=text(r["name"]) or "",
                            schema_name=db,
                            type="trigger",
                            parent=text(r["table_name"]),
                            owner=text(r["definer"]),
                            extra={"timing": text(r["timing"]), "event": text(r["event_name"])},
                        )
                    )
            elif kind == "constraint":
                for r in await self._query(Q.CONSTRAINTS.format(table_filter=""), [db]):
                    out.append(
                        ObjectInfo(
                            name=text(r["name"]) or "",
                            schema_name=db,
                            type="constraint",
                            parent=text(r["table_name"]),
                            extra={"constraint_type": text(r["constraint_type"]), "columns": text(r["columns_list"])},
                        )
                    )
            elif object_type:
                raise AdapterNotSupported(
                    Capability.OBJECTS, f"object type {kind!r} does not exist in {self.display_name}"
                )
        return out

    async def get_object_detail(self, database: str, schema: str, name: str, object_type: str) -> ObjectDetail:
        db = schema or database
        cols = [
            ColumnInfo(
                name=text(c["name"]) or "",
                data_type=text(c["data_type"]) or "",
                nullable=c["nullable"] == "YES",
                default=text(c["default_value"]),
                position=int(c["position"]),
                comment=text(c["comment_text"]) or None,
            )
            for c in await self._query(Q.COLUMNS, [db, name])
        ]
        obj = ObjectInfo(name=name, schema_name=db, type=object_type)
        stats: dict[str, Any] = {}
        if object_type in ("table", "view"):
            t = await self._one(Q.TABLE_ONE, [db, name])
            if t:
                obj.row_estimate = int(t["row_estimate"]) if t["row_estimate"] is not None else None
                obj.size_bytes = int(t["data_bytes"] or 0) + int(t["index_bytes"] or 0)
                stats = jsonable(dict(t))
        indexes = [
            {
                "name": text(r["name"]),
                "columns": text(r["columns_list"]),
                "unique": not int(r["non_unique"] or 0),
                "type": text(r["index_type"]),
                "cardinality": r["cardinality"],
            }
            for r in await self._query(Q.INDEXES, [db])
            if text(r["table_name"]) == name
        ]
        constraints = [
            jsonable(dict(r))
            for r in await self._query(Q.CONSTRAINTS.format(table_filter="AND tc.TABLE_NAME = %s"), [db, name])
        ]
        triggers = [jsonable(dict(r)) for r in await self._query(Q.TRIGGERS, [db]) if text(r["table_name"]) == name]
        show = {
            "table": "SHOW CREATE TABLE",
            "view": "SHOW CREATE VIEW",
            "procedure": "SHOW CREATE PROCEDURE",
            "function": "SHOW CREATE FUNCTION",
            "trigger": "SHOW CREATE TRIGGER",
            "sequence": "SHOW CREATE SEQUENCE",
        }.get(object_type)
        definition, note = None, None
        if show:
            try:
                row = await self._one(f"{show} {qident(db)}.{qident(name)}")
                if row:
                    keys = (
                        "create table",
                        "create view",
                        "create procedure",
                        "create function",
                        "sql original statement",
                    )
                    definition = next((text(row[k]) for k in keys if row.get(k)), None)
                    if definition is None:
                        note = "the definition is hidden: the account needs SHOW_ROUTINE / SHOW VIEW or must be the definer"
            except AdapterPermissionError:
                note = "the account is not allowed to see this definition"
            except AdapterError as exc:
                note = str(exc)
        return ObjectDetail(
            object=obj,
            columns=cols,
            indexes=indexes,
            constraints=constraints,
            triggers=triggers,
            statistics=stats,
            definition=definition,
            definition_note=note,
        )

    # ---- accounts ----------------------------------------------------------------------------
    async def _all_accounts(self) -> list[DbUser]:
        rows = await self._account_rows()
        if rows is None:
            return await self._accounts_from_information_schema()
        edges = await self._role_edges() or []
        dynamic = await self._dynamic_grants()
        members: dict[str, list[str]] = {}
        for member, role in edges:
            members.setdefault(member, []).append(role)
        out = []
        for r in rows:
            user, host = text(r["user"]) or "", text(r["host"]) or ""
            is_role = bool(r.get("is_role"))
            name = user if is_role and not host else account(user, host)
            reasons = [label for col, label in ADMIN_PRIVS.items() if (text(r.get(col.lower())) or "N") == "Y"]
            all_core = all(
                (text(r.get(c.lower())) or "N") == "Y"
                for c in ("Select_priv", "Insert_priv", "Super_priv", "Grant_priv")
            )
            dyn = sorted(set(dynamic.get(account(user, host), [])) & POWERFUL_DYNAMIC)
            reasons += [f"{p} (dynamic privilege)" for p in dyn]
            changed = utc(r.get("password_last_changed"))
            lifetime = r.get("password_lifetime")
            valid_until = changed + timedelta(days=int(lifetime)) if changed and lifetime else None
            out.append(
                DbUser(
                    name=name,
                    can_login=not is_role and not r.get("locked"),
                    is_superuser=(text(r.get("super_priv")) or "N") == "Y" or all_core,
                    attributes=jsonable(
                        {
                            "user": user,
                            "host": host,
                            "is_role": is_role,
                            "account_locked": bool(r.get("locked")),
                            "password_expired": (text(r.get("password_expired")) or "N") == "Y",
                            "auth_plugin": r.get("plugin"),
                            "has_password": bool(r.get("has_password")),
                            "password_last_changed": changed,
                            "ssl_type": r.get("ssl_type"),
                            "wildcard_host": host == "%",
                            "sensitive_privileges": [
                                label
                                for col, label in SENSITIVE_PRIVS.items()
                                if (text(r.get(col.lower())) or "N") == "Y"
                            ],
                            "anonymous": user == "",
                        }
                    ),
                    member_of=sorted(members.get(name, [])),
                    valid_until=valid_until,
                    connection_limit=int(r["max_user_connections"]) if r.get("max_user_connections") else None,
                    is_system=user in SYSTEM_ACCOUNTS,
                    admin_reasons=reasons,
                )
            )
        return out

    async def _accounts_from_information_schema(self) -> list[DbUser]:
        by: dict[str, list[str]] = {}
        for r in await self._query(Q.USER_PRIVILEGES):
            by.setdefault(normalize_grantee(r["grantee"]), []).append(text(r["privilege"]) or "")
        out = []
        for name, privs in by.items():
            reasons = [p for p in privs if p in ("SUPER", "FILE", "CREATE USER", "SHUTDOWN", "RELOAD", "PROCESS")]
            out.append(
                DbUser(
                    name=name,
                    can_login=True,
                    is_superuser="SUPER" in privs,
                    attributes={"source": "information_schema.USER_PRIVILEGES (grant tables not readable)"},
                    is_system=name.split("@")[0] in SYSTEM_ACCOUNTS,
                    admin_reasons=reasons,
                )
            )
        return out

    async def list_users(self) -> list[DbUser]:
        return [u for u in await self._all_accounts() if not u.attributes.get("is_role")]

    async def list_roles(self) -> list[DbUser]:
        return [u for u in await self._all_accounts() if u.attributes.get("is_role")]

    async def get_privileges(
        self, database: str | None = None, grantee: str | None = None, limit: int = 2000
    ) -> list[Privilege]:
        out: list[Privilege] = []
        where_db = "WHERE TABLE_SCHEMA = %s" if database else ""
        args = [database] if database else []
        if not database:
            for r in await self._query(Q.USER_PRIVILEGES):
                out.append(
                    Privilege(
                        grantee=normalize_grantee(r["grantee"]),
                        object_type="server",
                        object_name="*.*",
                        privilege=text(r["privilege"]) or "",
                        grantable=r["grantable"] == "YES",
                    )
                )
        for r in await self._query(Q.SCHEMA_PRIVILEGES.format(where=where_db), args):
            out.append(
                Privilege(
                    grantee=normalize_grantee(r["grantee"]),
                    object_type="database",
                    object_name=f"{text(r['db'])}.*",
                    privilege=text(r["privilege"]) or "",
                    grantable=r["grantable"] == "YES",
                    database=text(r["db"]),
                )
            )
        for r in await self._query(Q.TABLE_PRIVILEGES.format(where=where_db), args):
            out.append(
                Privilege(
                    grantee=normalize_grantee(r["grantee"]),
                    object_type="table",
                    object_name=f"{text(r['db'])}.{text(r['table_name'])}",
                    privilege=text(r["privilege"]) or "",
                    grantable=r["grantable"] == "YES",
                    database=text(r["db"]),
                )
            )
        for r in await self._query(Q.COLUMN_PRIVILEGES.format(where=where_db), args):
            out.append(
                Privilege(
                    grantee=normalize_grantee(r["grantee"]),
                    object_type="column",
                    object_name=f"{text(r['db'])}.{text(r['table_name'])}.{text(r['column_name'])}",
                    privilege=text(r["privilege"]) or "",
                    grantable=r["grantable"] == "YES",
                    database=text(r["db"]),
                )
            )
        try:
            for member, role in await self._role_edges() or []:
                out.append(Privilege(grantee=member, object_type="role", object_name=role, privilege="MEMBER"))
        except AdapterObjectMissing:
            pass
        if grantee:
            g = grantee.lower()
            out = [p for p in out if p.grantee.lower() == g or p.grantee.lower().startswith(g + "@")]
        return out[:limit]
