"""Oracle statements for the guarded administration actions.

* Oracle identifiers are created upper-case (the unquoted-identifier convention), quoted.
* Passwords never appear in SQL text: CREATE USER runs inside an anonymous PL/SQL block that
  receives the password as a bind variable and quotes it with ``DBMS_ASSERT.ENQUOTE_NAME``, so
  neither the shared pool (V$SQL) nor the audit trail sees the literal.
* PDB lifecycle (CREATE/DROP PLUGGABLE DATABASE) needs storage decisions (file name
  conversion, keystore) and is deliberately not offered: use DBCA or SQL*Plus.
* In Oracle a schema is a user: "create schema" is offered as "create user".
"""

from __future__ import annotations

from typing import Any

from app.adapters import admin as A
from app.adapters.base import AdapterNotSupported, Capability
from app.adapters.oracle.base import qid

OBJECT_PRIVS = {"SELECT", "INSERT", "UPDATE", "DELETE", "EXECUTE", "REFERENCES", "ALTER", "INDEX", "READ", "DEBUG"}
#: system privileges this API may grant; powerful ones (ANY, ALTER SYSTEM, ...) stay in the SQL workspace
SYSTEM_PRIVS = {
    "CREATE SESSION",
    "CREATE TABLE",
    "CREATE VIEW",
    "CREATE SEQUENCE",
    "CREATE PROCEDURE",
    "CREATE TRIGGER",
    "CREATE SYNONYM",
    "CREATE TYPE",
    "CREATE MATERIALIZED VIEW",
    "SELECT ANY DICTIONARY",
}
GRANTABLE_ROLES = {"CONNECT", "RESOURCE", "SELECT_CATALOG_ROLE"}


def _name(v: Any, what: str) -> str:
    return A.ident(v, what).upper()


def _password(p: dict[str, Any]) -> str:
    pw = A.password(p)
    if '"' in pw:
        raise A.AdminValidationError("Oracle passwords cannot contain double quotes")
    if len(pw.encode("utf-8")) > 30:
        # 30 bytes is the limit up to 21c (23ai allows more; kept portable)
        raise A.AdminValidationError("Oracle passwords are limited to 30 bytes")
    return pw


def build(action: str, p: dict[str, Any]) -> A.AdminStatement:
    spec = A.ACTIONS.get(action)
    if spec is None:
        raise AdapterNotSupported(Capability.ADMINISTRATION, f"unknown action {action!r}")
    risk = spec["risk"]
    if action in ("create_database", "drop_database"):
        raise AdapterNotSupported(
            Capability.ADMINISTRATION,
            "pluggable database lifecycle is not offered (storage/keystore decisions): use DBCA or SQL*Plus",
        )
    if action == "create_schema":
        raise AdapterNotSupported(Capability.ADMINISTRATION, "in Oracle a schema is a user: use Create user")
    if action == "create_user":
        A.require(p, "name", "password")
        name = _name(p["name"], "user name")
        pw = _password(p)
        ts = _name(p["default_tablespace"], "tablespace") if p.get("default_tablespace") else None
        tail = f" DEFAULT TABLESPACE {qid(ts)} QUOTA UNLIMITED ON {qid(ts)}" if ts else ""
        block = (
            "BEGIN EXECUTE IMMEDIATE 'CREATE USER ' || DBMS_ASSERT.ENQUOTE_NAME(:1, FALSE) || "
            f"' IDENTIFIED BY ' || DBMS_ASSERT.ENQUOTE_NAME(:2, FALSE) || '{tail}'; END;"
        )
        grant = f"GRANT CREATE SESSION TO {qid(name)}"
        return A.AdminStatement(
            action,
            risk,
            [block, grant],
            [(name, pw), None],
            [f'CREATE USER {qid(name)} IDENTIFIED BY "********"{tail}', grant],
            target_object=name,
            notes=[
                "The password is passed as a bind variable and never appears in SQL text, V$SQL or the audit trail.",
                "The user receives CREATE SESSION only; grant further privileges explicitly.",
            ],
        )
    if action == "drop_user":
        name = _name(p.get("name"), "user name")
        cascade = bool(p.get("cascade"))
        sql = f"DROP USER {qid(name)}{' CASCADE' if cascade else ''}"
        notes = (
            ["CASCADE drops every object the user owns."]
            if cascade
            else ["Fails if the user owns objects (use cascade)."]
        )
        return A.AdminStatement(action, risk, [sql], [None], [sql], target_object=name, notes=notes)
    if action == "create_role":
        name = _name(p.get("name"), "role name")
        sql = f"CREATE ROLE {qid(name)}"
        return A.AdminStatement(action, risk, [sql], [None], [sql], target_object=name)
    if action in ("grant", "revoke"):
        A.require(p, "object_type", "grantee")
        grantee = _name(p["grantee"], "grantee")
        verb, prep = ("GRANT", "TO") if action == "grant" else ("REVOKE", "FROM")
        otype = str(p["object_type"]).lower()
        if otype == "role":
            role = _name(p.get("role") or (p.get("privileges") or [""])[0], "role name")
            if role in ("DBA", "IMP_FULL_DATABASE", "DATAPUMP_IMP_FULL_DATABASE") and action == "grant":
                raise A.AdminValidationError(f"granting {role} is not offered here; use the SQL workspace with review")
            sql = f"{verb} {qid(role)} {prep} {qid(grantee)}"
            return A.AdminStatement(action, risk, [sql], [None], [sql], target_object=role)
        if otype in ("system", "database", "server"):
            privs = A.privileges(p, SYSTEM_PRIVS)
            sql = f"{verb} {', '.join(privs)} {prep} {qid(grantee)}"
            return A.AdminStatement(action, risk, [sql], [None], [sql], target_object="DATABASE")
        if otype in ("table", "view", "sequence", "procedure", "function", "package"):
            A.require(p, "schema", "object_name")
            owner, obj = _name(p["schema"], "schema"), _name(p["object_name"], "object")
            privs = A.privileges(p, OBJECT_PRIVS)
            sql = f"{verb} {', '.join(privs)} ON {qid(owner)}.{qid(obj)} {prep} {qid(grantee)}"
            return A.AdminStatement(action, risk, [sql], [None], [sql], target_object=f"{owner}.{obj}")
        raise A.AdminValidationError(
            "object_type must be system, role, table, view, sequence, procedure, function or package"
        )
    if action == "create_index":
        A.require(p, "schema", "table", "name", "columns")
        owner = _name(p["schema"], "schema")
        table, name = _name(p["table"], "table"), _name(p["name"], "index name")
        cols = ", ".join(qid(c.upper()) for c in A.columns(p))
        unique = "UNIQUE " if p.get("unique") else ""
        online = " ONLINE" if p.get("online") else ""
        sql = f"CREATE {unique}INDEX {qid(owner)}.{qid(name)} ON {qid(owner)}.{qid(table)} ({cols}){online}"
        notes = (
            ["ONLINE index builds require Enterprise Edition."]
            if online
            else ["Offline build: DML on the table waits for the build (DDL_LOCK_TIMEOUT applies)."]
        )
        return A.AdminStatement(
            action, risk, [sql], [None], [sql], notes=notes, target_object=f"{owner}.{table}.{name}"
        )
    raise AdapterNotSupported(Capability.ADMINISTRATION, f"{action} is not available for Oracle")
