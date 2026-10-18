"""SQL Server statements for the guarded administration actions (T-SQL)."""

from __future__ import annotations

from typing import Any

from app.adapters import admin as A
from app.adapters.base import AdapterNotSupported, Capability
from app.adapters.mssql.base import qn

DATABASE_PERMS = {
    "CONNECT",
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "EXECUTE",
    "VIEW DEFINITION",
    "SHOWPLAN",
    "CREATE TABLE",
    "CREATE VIEW",
    "CREATE PROCEDURE",
}
SCHEMA_PERMS = {"SELECT", "INSERT", "UPDATE", "DELETE", "EXECUTE", "ALTER", "VIEW DEFINITION", "REFERENCES"}
OBJECT_PERMS = {"SELECT", "INSERT", "UPDATE", "DELETE", "EXECUTE", "REFERENCES", "VIEW DEFINITION"}
SERVER_PERMS = {"VIEW SERVER STATE", "VIEW ANY DEFINITION", "VIEW ANY DATABASE", "CONNECT ANY DATABASE"}


def _nstr(value: str) -> str:
    return "N'" + value.replace("'", "''") + "'"


def build(action: str, p: dict[str, Any]) -> A.AdminStatement:
    spec = A.ACTIONS.get(action)
    if spec is None:
        raise AdapterNotSupported(Capability.ADMINISTRATION, f"unknown action {action!r}")
    risk = spec["risk"]
    if action == "create_database":
        name = A.ident(p.get("name"), "database name")
        sql = f"CREATE DATABASE {qn(name)}"
        return A.AdminStatement(
            action,
            risk,
            [sql],
            [None],
            [sql],
            target_object=name,
            notes=["Files are created in the instance's default data/log locations."],
        )
    if action == "drop_database":
        name = A.ident(p.get("name"), "database name")
        sql = f"DROP DATABASE {qn(name)}"
        return A.AdminStatement(
            action, risk, [sql], [None], [sql], target_object=name, notes=["Fails while sessions use the database."]
        )
    if action == "create_schema":
        A.require(p, "database", "name")
        db, name = A.ident(p["database"], "database"), A.ident(p["name"], "schema name")
        sql = f"CREATE SCHEMA {qn(name)}" + (
            f" AUTHORIZATION {qn(A.ident(p['owner'], 'owner'))}" if p.get("owner") else ""
        )
        return A.AdminStatement(action, risk, [sql], [None], [sql], database=db, target_object=f"{db}.{name}")
    if action == "create_user":
        A.require(p, "name", "password")
        name = A.ident(p["name"], "login name")
        pw = A.password(p)
        sql = [f"CREATE LOGIN {qn(name)} WITH PASSWORD = {_nstr(pw)}, CHECK_POLICY = ON, CHECK_EXPIRATION = OFF"]
        shown = [f"CREATE LOGIN {qn(name)} WITH PASSWORD = N'********', CHECK_POLICY = ON, CHECK_EXPIRATION = OFF"]
        db = p.get("database")
        notes = ["SQL Server masks the password of CREATE LOGIN in traces and Extended Events."]
        dbs: list[str | None] = [None]
        if db:
            A.ident(db, "database")
            sql.append(f"CREATE USER {qn(name)} FOR LOGIN {qn(name)}")
            shown.append(f"-- in database {qn(db)}\n{sql[-1]}")
            dbs.append(db)
            notes.append(f"A database user is created in {db}.")
        return A.AdminStatement(
            action, risk, sql, [None] * len(sql), shown, notes=notes, target_object=name, statement_databases=dbs
        )
    if action == "drop_user":
        name = A.ident(p.get("name"), "login name")
        sql = f"DROP LOGIN {qn(name)}"
        return A.AdminStatement(
            action,
            risk,
            [sql],
            [None],
            [sql],
            target_object=name,
            notes=["Database users mapped to the login remain (orphaned) until dropped."],
        )
    if action == "create_role":
        A.require(p, "database", "name")
        db, name = A.ident(p["database"], "database"), A.ident(p["name"], "role name")
        sql = f"CREATE ROLE {qn(name)}"
        return A.AdminStatement(action, risk, [sql], [None], [sql], database=db, target_object=f"{db}.{name}")
    if action in ("grant", "revoke"):
        A.require(p, "object_type", "grantee")
        grantee = A.ident(p["grantee"], "grantee")
        verb, prep = ("GRANT", "TO") if action == "grant" else ("REVOKE", "FROM")
        otype = str(p["object_type"]).lower()
        db = p.get("database")
        if otype == "role":
            A.require(p, "database")
            role = A.ident(p.get("role") or (p.get("privileges") or [""])[0], "role name")
            sql = f"ALTER ROLE {qn(role)} {'ADD' if action == 'grant' else 'DROP'} MEMBER {qn(grantee)}"
            return A.AdminStatement(
                action, risk, [sql], [None], [sql], database=A.ident(db, "database"), target_object=role
            )
        if otype == "server":
            privs = A.privileges(p, SERVER_PERMS)
            sql = f"{verb} {', '.join(privs)} {prep} {qn(grantee)}"
            return A.AdminStatement(action, risk, [sql], [None], [sql], target_object="SERVER")
        A.require(p, "database")
        db = A.ident(db, "database")
        if otype == "database":
            privs, on = A.privileges(p, DATABASE_PERMS), ""
        elif otype == "schema":
            privs, on = A.privileges(p, SCHEMA_PERMS), f" ON SCHEMA::{qn(A.ident(p.get('schema'), 'schema'))}"
        elif otype == "table":
            privs = A.privileges(p, OBJECT_PERMS)
            on = f" ON OBJECT::{qn(A.ident(p.get('schema') or 'dbo', 'schema'))}.{qn(A.ident(p.get('object_name'), 'object'))}"
        else:
            raise A.AdminValidationError("object_type must be server, database, schema, table or role")
        sql = f"{verb} {', '.join(privs)}{on} {prep} {qn(grantee)}"
        return A.AdminStatement(action, risk, [sql], [None], [sql], database=db, target_object=on.strip() or db)
    if action == "create_index":
        A.require(p, "database", "table", "name", "columns")
        db = A.ident(p["database"], "database")
        schema = A.ident(p.get("schema") or "dbo", "schema")
        table, name = A.ident(p["table"], "table"), A.ident(p["name"], "index name")
        cols = ", ".join(qn(c) for c in A.columns(p))
        include = ""
        if p.get("include"):
            include = " INCLUDE (" + ", ".join(qn(c) for c in A.columns({"columns": p["include"]})) + ")"
        unique = "UNIQUE " if p.get("unique") else ""
        online = " WITH (ONLINE = ON)" if p.get("online") else ""
        sql = f"CREATE {unique}NONCLUSTERED INDEX {qn(name)} ON {qn(schema)}.{qn(table)} ({cols}){include}{online}"
        notes = (
            ["ONLINE = ON requires Enterprise/Developer edition or Azure SQL."]
            if online
            else ["Offline build: the table is locked against writes while the index is built."]
        )
        return A.AdminStatement(
            action, risk, [sql], [None], [sql], database=db, notes=notes, target_object=f"{schema}.{table}.{name}"
        )
    raise AdapterNotSupported(Capability.ADMINISTRATION, f"{action} is not available for SQL Server")
