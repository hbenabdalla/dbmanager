"""PostgreSQL statements for the guarded administration actions.

Passwords are never sent in clear text: a SCRAM-SHA-256 verifier is computed here and
sent instead (``PASSWORD 'SCRAM-SHA-256$…'``), so neither the server log nor
``pg_stat_statements`` can ever contain the password.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Any

from app.adapters import admin as A
from app.adapters.base import AdapterNotSupported, Capability

TABLE_PRIVS = {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"}
SCHEMA_PRIVS = {"USAGE", "CREATE"}
DATABASE_PRIVS = {"CONNECT", "CREATE", "TEMPORARY"}


def qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def scram_verifier(password: str, iterations: int = 4096) -> str:
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def build(action: str, p: dict[str, Any]) -> A.AdminStatement:
    spec = A.ACTIONS.get(action)
    if spec is None:
        raise AdapterNotSupported(Capability.ADMINISTRATION, f"unknown action {action!r}")
    risk = spec["risk"]
    if action == "create_database":
        name = A.ident(p.get("name"), "database name")
        sql = f"CREATE DATABASE {qi(name)}"
        if p.get("owner"):
            sql += f" OWNER {qi(A.ident(p['owner'], 'owner'))}"
        return A.AdminStatement(action, risk, [sql], [None], [sql], target_object=name)
    if action == "drop_database":
        name = A.ident(p.get("name"), "database name")
        sql = f"DROP DATABASE {qi(name)}"
        return A.AdminStatement(
            action, risk, [sql], [None], [sql], notes=["Fails while sessions are connected."], target_object=name
        )
    if action == "create_schema":
        A.require(p, "database", "name")
        db, name = A.ident(p["database"], "database"), A.ident(p["name"], "schema name")
        sql = f"CREATE SCHEMA {qi(name)}"
        if p.get("owner"):
            sql += f" AUTHORIZATION {qi(A.ident(p['owner'], 'owner'))}"
        return A.AdminStatement(action, risk, [sql], [None], [sql], database=db, target_object=f"{db}.{name}")
    if action == "create_user":
        A.require(p, "name", "password")
        name = A.ident(p["name"], "user name")
        verifier = scram_verifier(A.password(p))
        sql = f"CREATE ROLE {qi(name)} LOGIN PASSWORD {A.sql_string(verifier)}"
        return A.AdminStatement(
            action,
            risk,
            [sql],
            [None],
            [f"CREATE ROLE {qi(name)} LOGIN PASSWORD '********'"],
            notes=["The password is sent as a SCRAM-SHA-256 verifier computed by DBManager, never in clear text."],
            target_object=name,
        )
    if action == "drop_user":
        name = A.ident(p.get("name"), "role name")
        sql = f"DROP ROLE {qi(name)}"
        return A.AdminStatement(
            action,
            risk,
            [sql],
            [None],
            [sql],
            notes=["Fails while the role still owns objects or holds grants."],
            target_object=name,
        )
    if action == "create_role":
        name = A.ident(p.get("name"), "role name")
        sql = f"CREATE ROLE {qi(name)} NOLOGIN"
        return A.AdminStatement(action, risk, [sql], [None], [sql], target_object=name)
    if action in ("grant", "revoke"):
        A.require(p, "object_type", "grantee")
        grantee = A.ident(p["grantee"], "grantee")
        verb, prep = ("GRANT", "TO") if action == "grant" else ("REVOKE", "FROM")
        otype = str(p["object_type"]).lower()
        db = p.get("database")
        if otype == "role":
            role = A.ident(p.get("role") or (p.get("privileges") or [""])[0], "role name")
            sql = f"{verb} {qi(role)} {prep} {qi(grantee)}"
            return A.AdminStatement(action, risk, [sql], [None], [sql], target_object=role)
        if otype == "database":
            privs = A.privileges(p, DATABASE_PRIVS)
            target = f"DATABASE {qi(A.ident(db, 'database'))}"
        elif otype == "schema":
            privs = A.privileges(p, SCHEMA_PRIVS)
            target = f"SCHEMA {qi(A.ident(p.get('schema'), 'schema'))}"
        elif otype == "table":
            privs = A.privileges(p, TABLE_PRIVS)
            target = f"TABLE {qi(A.ident(p.get('schema') or 'public', 'schema'))}.{qi(A.ident(p.get('object_name'), 'table'))}"
        else:
            raise A.AdminValidationError("object_type must be database, schema, table or role")
        sql = f"{verb} {', '.join(privs)} ON {target} {prep} {qi(grantee)}"
        return A.AdminStatement(
            action, risk, [sql], [None], [sql], database=db if otype != "database" else None, target_object=target
        )
    if action == "create_index":
        A.require(p, "database", "table", "name", "columns")
        db = A.ident(p["database"], "database")
        schema = A.ident(p.get("schema") or "public", "schema")
        table, name = A.ident(p["table"], "table"), A.ident(p["name"], "index name")
        cols = ", ".join(qi(c) for c in A.columns(p))
        unique = "UNIQUE " if p.get("unique") else ""
        sql = f"CREATE {unique}INDEX CONCURRENTLY {qi(name)} ON {qi(schema)}.{qi(table)} ({cols})"
        return A.AdminStatement(
            action,
            risk,
            [sql],
            [None],
            [sql],
            database=db,
            notes=[
                "CONCURRENTLY builds the index without blocking writes; a failed build leaves an INVALID index to drop."
            ],
            target_object=f"{schema}.{table}.{name}",
        )
    raise AdapterNotSupported(Capability.ADMINISTRATION, f"{action} is not available for PostgreSQL")
