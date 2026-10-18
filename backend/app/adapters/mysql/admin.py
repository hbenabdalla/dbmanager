"""MySQL / MariaDB statements for the guarded administration actions."""

from __future__ import annotations

import re
from typing import Any

from app.adapters import admin as A
from app.adapters.base import AdapterNotSupported, Capability
from app.adapters.mysql.base import qident

GRANTABLE = {
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "EXECUTE",
    "CREATE",
    "ALTER",
    "DROP",
    "INDEX",
    "CREATE VIEW",
    "SHOW VIEW",
    "TRIGGER",
    "REFERENCES",
    "CREATE ROUTINE",
    "ALTER ROUTINE",
    "EVENT",
    "LOCK TABLES",
    "CREATE TEMPORARY TABLES",
}
GLOBAL_ONLY = {"PROCESS", "REPLICATION CLIENT", "SHOW DATABASES"}
CHARSET_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
HOST_RE = re.compile(r"^[A-Za-z0-9_.%:\-]{1,255}$")


def _account(value: Any, *, default_host: str | None = "%") -> tuple[str, str | None]:
    raw = str(value or "").strip()
    if "@" in raw:
        user, host = raw.rsplit("@", 1)
    else:
        user, host = raw, default_host
    A.ident(user, "account name")
    if host is not None and not HOST_RE.match(host):
        raise A.AdminValidationError(f"invalid host {host!r}")
    return user, host


def _acct_sql(user: str, host: str | None) -> tuple[str, tuple[Any, ...], str]:
    if host is None:
        return "%s", (user,), f"'{user}'"
    return "%s@%s", (user, host), f"'{user}'@'{host}'"


def build(action: str, p: dict[str, Any], *, flavor: str) -> A.AdminStatement:
    spec = A.ACTIONS.get(action)
    if spec is None:
        raise AdapterNotSupported(Capability.ADMINISTRATION, f"unknown action {action!r}")
    risk = spec["risk"]
    if action in ("create_database", "create_schema"):
        A.require(p, "name")
        name = A.ident(p["name"], "database name")
        sql = f"CREATE DATABASE {qident(name)}"
        for key, kw in (("charset", "CHARACTER SET"), ("collation", "COLLATE")):
            if p.get(key):
                if not CHARSET_RE.match(str(p[key])):
                    raise A.AdminValidationError(f"invalid {key}")
                sql += f" {kw} {p[key]}"
        notes = ["In MySQL and MariaDB a schema is a database."] if action == "create_schema" else []
        return A.AdminStatement(action, risk, [sql], [None], [sql], notes=notes, target_object=name)
    if action == "drop_database":
        name = A.ident(p.get("name"), "database name")
        sql = f"DROP DATABASE {qident(name)}"
        return A.AdminStatement(
            action,
            risk,
            [sql],
            [None],
            [sql],
            notes=["Drops every table, view and routine in the database."],
            target_object=name,
        )
    if action == "create_user":
        A.require(p, "name", "password")
        user, host = _account(p["name"])
        pw = A.password(p)
        acct, params, shown = _acct_sql(user, host)
        tail = " REQUIRE SSL" if p.get("require_tls") else ""
        sql = f"CREATE USER {acct} IDENTIFIED BY %s{tail}"
        return A.AdminStatement(
            action,
            risk,
            [sql],
            [(*params, pw)],
            [f"CREATE USER {shown} IDENTIFIED BY '********'{tail}"],
            target_object=f"{user}@{host}",
        )
    if action == "drop_user":
        user, host = _account(p.get("name"))
        acct, params, shown = _acct_sql(user, host)
        return A.AdminStatement(
            action, risk, [f"DROP USER {acct}"], [params], [f"DROP USER {shown}"], target_object=f"{user}@{host}"
        )
    if action == "create_role":
        name = A.ident(p.get("name"), "role name")
        return A.AdminStatement(
            action, risk, ["CREATE ROLE %s"], [(name,)], [f"CREATE ROLE '{name}'"], target_object=name
        )
    if action in ("grant", "revoke"):
        A.require(p, "object_type", "grantee")
        user, host = _account(p["grantee"], default_host=None if flavor == "mariadb" else "%")
        acct, params, shown = _acct_sql(user, host)
        verb, prep = ("GRANT", "TO") if action == "grant" else ("REVOKE", "FROM")
        otype = str(p["object_type"]).lower()
        if otype == "role":
            role = A.ident(p.get("role") or (p.get("privileges") or [""])[0], "role name")
            sql = f"{verb} %s {prep} {acct}"
            return A.AdminStatement(
                action, risk, [sql], [(role, *params)], [f"{verb} '{role}' {prep} {shown}"], target_object=role
            )
        if otype == "server":
            privs = A.privileges(p, GLOBAL_ONLY)
            target = "*.*"
        elif otype == "database":
            privs = A.privileges(p, GRANTABLE)
            target = f"{qident(A.ident(p.get('database'), 'database name'))}.*"
        elif otype == "table":
            privs = A.privileges(
                p,
                GRANTABLE
                - {"CREATE ROUTINE", "ALTER ROUTINE", "EVENT", "LOCK TABLES", "CREATE TEMPORARY TABLES", "EXECUTE"},
            )
            target = f"{qident(A.ident(p.get('database'), 'database name'))}.{qident(A.ident(p.get('object_name'), 'table'))}"
        else:
            raise A.AdminValidationError("object_type must be server, database, table or role")
        sql = f"{verb} {', '.join(privs)} ON {target} {prep} {acct}"
        return A.AdminStatement(
            action,
            risk,
            [sql],
            [params],
            [f"{verb} {', '.join(privs)} ON {target} {prep} {shown}"],
            target_object=target,
        )
    if action == "create_index":
        A.require(p, "database", "table", "name", "columns")
        db, table, name = (A.ident(p[k], k) for k in ("database", "table", "name"))
        cols = A.columns(p)
        unique = "UNIQUE " if p.get("unique") else ""
        sql = (
            f"CREATE {unique}INDEX {qident(name)} ON {qident(db)}.{qident(table)} "
            f"({', '.join(qident(c) for c in cols)}) ALGORITHM=INPLACE LOCK=NONE"
        )
        return A.AdminStatement(
            action,
            risk,
            [sql],
            [None],
            [sql],
            database=db,
            notes=[
                "ALGORITHM=INPLACE LOCK=NONE: the statement fails instead of blocking writes if an online build is impossible."
            ],
            target_object=f"{db}.{table}.{name}",
        )
    raise AdapterNotSupported(Capability.ADMINISTRATION, f"{action} is not available for this engine")
