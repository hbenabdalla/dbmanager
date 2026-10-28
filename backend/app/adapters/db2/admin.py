"""Db2 LUW statements for the guarded administration actions.

* Databases are created and dropped with CLP/instance commands (``CREATE DATABASE``), not SQL:
  not offered.
* Db2 does not store users: authentication is external (OS, LDAP, Kerberos). "Create user" is
  therefore not offered; access is granted to an existing authorization ID with GRANT CONNECT.
* REORG and RUNSTATS are only ever *recommended* by DBManager, never executed.
"""

from __future__ import annotations

from typing import Any

from app.adapters import admin as A
from app.adapters.base import AdapterNotSupported, Capability
from app.adapters.db2.base import qid

DATABASE_AUTHORITIES = {"CONNECT", "CREATETAB", "BINDADD", "IMPLICIT_SCHEMA", "LOAD", "EXPLAIN", "SQLADM", "WLMADM"}
SCHEMA_PRIVS = {"CREATEIN", "ALTERIN", "DROPIN", "SELECTIN", "INSERTIN", "UPDATEIN", "DELETEIN", "EXECUTEIN"}
TABLE_PRIVS = {"SELECT", "INSERT", "UPDATE", "DELETE", "ALTER", "INDEX", "REFERENCES"}
GRANTEE_KINDS = {"user": "USER", "group": "GROUP", "role": "ROLE"}


def _name(v: Any, what: str) -> str:
    return A.ident(v, what).upper()


def _grantee(p: dict[str, Any]) -> str:
    kind = GRANTEE_KINDS.get(str(p.get("grantee_type") or "user").lower())
    if kind is None:
        raise A.AdminValidationError("grantee_type must be user, group or role")
    return f"{kind} {qid(_name(p['grantee'], 'grantee'))}"


def build(action: str, p: dict[str, Any]) -> A.AdminStatement:
    spec = A.ACTIONS.get(action)
    if spec is None:
        raise AdapterNotSupported(Capability.ADMINISTRATION, f"unknown action {action!r}")
    risk = spec["risk"]
    if action in ("create_database", "drop_database"):
        raise AdapterNotSupported(
            Capability.ADMINISTRATION, "Db2 databases are created/dropped with CLP instance commands"
        )
    if action in ("create_user", "drop_user"):
        raise AdapterNotSupported(
            Capability.ADMINISTRATION,
            "Db2 authenticates users outside the database (OS/LDAP/Kerberos); grant CONNECT to an existing ID instead",
        )
    if action == "create_schema":
        name = _name(p.get("name"), "schema name")
        owner = f" AUTHORIZATION {qid(_name(p['owner'], 'owner'))}" if p.get("owner") else ""
        sql = f"CREATE SCHEMA {qid(name)}{owner}"
        return A.AdminStatement(action, risk, [sql], [None], [sql], target_object=name)
    if action == "create_role":
        name = _name(p.get("name"), "role name")
        sql = f"CREATE ROLE {qid(name)}"
        return A.AdminStatement(action, risk, [sql], [None], [sql], target_object=name)
    if action in ("grant", "revoke"):
        A.require(p, "object_type", "grantee")
        verb, prep = ("GRANT", "TO") if action == "grant" else ("REVOKE", "FROM")
        grantee = _grantee(p)
        otype = str(p["object_type"]).lower()
        if otype == "role":
            role = _name(p.get("role") or (p.get("privileges") or [""])[0], "role name")
            sql = f"{verb} ROLE {qid(role)} {prep} {grantee}"
            return A.AdminStatement(action, risk, [sql], [None], [sql], target_object=role)
        if otype == "database":
            privs = A.privileges(p, DATABASE_AUTHORITIES)
            sql = f"{verb} {', '.join(privs)} ON DATABASE {prep} {grantee}"
            return A.AdminStatement(action, risk, [sql], [None], [sql], target_object="DATABASE")
        if otype == "schema":
            schema = _name(p.get("schema"), "schema")
            privs = A.privileges(p, SCHEMA_PRIVS)
            sql = f"{verb} {', '.join(privs)} ON SCHEMA {qid(schema)} {prep} {grantee}"
            return A.AdminStatement(
                action,
                risk,
                [sql],
                [None],
                [sql],
                target_object=schema,
                notes=["SELECTIN/INSERTIN/UPDATEIN/DELETEIN/EXECUTEIN need Db2 11.5 or later."],
            )
        if otype == "table":
            schema, table = _name(p.get("schema"), "schema"), _name(p.get("object_name"), "table")
            privs = A.privileges(p, TABLE_PRIVS)
            sql = f"{verb} {', '.join(privs)} ON TABLE {qid(schema)}.{qid(table)} {prep} {grantee}"
            return A.AdminStatement(action, risk, [sql], [None], [sql], target_object=f"{schema}.{table}")
        raise A.AdminValidationError("object_type must be database, schema, table or role")
    if action == "create_index":
        A.require(p, "schema", "table", "name", "columns")
        schema, table, name = _name(p["schema"], "schema"), _name(p["table"], "table"), _name(p["name"], "index name")
        cols = ", ".join(qid(c.upper()) for c in A.columns(p))
        unique = "UNIQUE " if p.get("unique") else ""
        sql = f"CREATE {unique}INDEX {qid(schema)}.{qid(name)} ON {qid(schema)}.{qid(table)} ({cols})"
        return A.AdminStatement(
            action,
            risk,
            [sql],
            [None],
            [sql],
            target_object=f"{schema}.{table}.{name}",
            notes=["Run RUNSTATS on the table afterwards (KBM DBManager recommends, it does not run it)."],
        )
    raise AdapterNotSupported(Capability.ADMINISTRATION, f"{action} is not available for Db2")
