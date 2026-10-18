"""Guarded administration actions (create database / schema / user / role, grant, revoke,
create index, drop user).

The platform never assembles one generic SQL statement for all engines: every adapter builds
its own vendor-specific statements from a validated request (:meth:`build_admin`). The API
shows the generated statement (passwords masked) for review, requires the permission and
typed confirmation for the risk class, audits the outcome, and only then executes it with the
server's *administration* credential — never on a server registered as Monitoring only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.adapters.base import AdapterError

# action -> (permission, risk class, description, required params)
ACTIONS: dict[str, dict[str, Any]] = {
    "create_database": {
        "permission": "database.create",
        "risk": "PRIVILEGED_CHANGE",
        "label": "Create database",
        "params": ["name"],
    },
    "drop_database": {
        "permission": "database.delete",
        "risk": "DESTRUCTIVE",
        "label": "Drop database",
        "params": ["name"],
    },
    "create_schema": {
        "permission": "schema.modify",
        "risk": "LOW_RISK_CHANGE",
        "label": "Create schema",
        "params": ["database", "name"],
    },
    "create_user": {
        "permission": "user.manage",
        "risk": "PRIVILEGED_CHANGE",
        "label": "Create user",
        "params": ["name", "password"],
    },
    "drop_user": {"permission": "user.manage", "risk": "DESTRUCTIVE", "label": "Drop user", "params": ["name"]},
    "create_role": {
        "permission": "user.manage",
        "risk": "PRIVILEGED_CHANGE",
        "label": "Create role",
        "params": ["name"],
    },
    "grant": {
        "permission": "privilege.manage",
        "risk": "PRIVILEGED_CHANGE",
        "label": "Grant privileges",
        "params": ["privileges", "object_type", "grantee"],
    },
    "revoke": {
        "permission": "privilege.manage",
        "risk": "DESTRUCTIVE",
        "label": "Revoke privileges",
        "params": ["privileges", "object_type", "grantee"],
    },
    "create_index": {
        "permission": "schema.modify",
        "risk": "LOW_RISK_CHANGE",
        "label": "Create index",
        "params": ["database", "table", "name", "columns"],
    },
}

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")


@dataclass
class AdminStatement:
    action: str
    risk: str
    statements: list[str]  # executed in order (may carry driver placeholders)
    params: list[tuple[Any, ...] | None]
    display: list[str]  # what the reviewer sees — secrets masked
    database: str | None = None
    notes: list[str] = field(default_factory=list)
    target_object: str | None = None
    #: per-statement database context (None entries use ``database``)
    statement_databases: list[str | None] | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "risk": self.risk,
            "statements": self.display,
            "database": self.database,
            "notes": self.notes,
            "target_object": self.target_object,
        }


class AdminValidationError(AdapterError):
    pass


def ident(value: Any, what: str = "identifier") -> str:
    """Validate an identifier. Quoted identifiers with special characters are deliberately
    not accepted through this API (use the SQL workspace for those)."""
    v = str(value or "").strip()
    if not IDENT_RE.match(v):
        raise AdminValidationError(
            f"invalid {what} {v!r}: use letters, digits, _ $ # (max 128, not starting with a digit)"
        )
    return v


def require(params: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if params.get(k) in (None, "", [])]
    if missing:
        raise AdminValidationError(f"missing parameters: {', '.join(missing)}")


def privileges(params: dict[str, Any], allowed: set[str]) -> list[str]:
    raw = params.get("privileges") or []
    if isinstance(raw, str):
        raw = [p.strip() for p in raw.split(",")]
    out = []
    for p in raw:
        v = str(p).strip().upper()
        if v not in allowed:
            raise AdminValidationError(f"privilege {v!r} is not allowed here (allowed: {', '.join(sorted(allowed))})")
        out.append(v)
    if not out:
        raise AdminValidationError("no privileges given")
    return out


def columns(params: dict[str, Any]) -> list[str]:
    raw = params.get("columns") or []
    if isinstance(raw, str):
        raw = [c.strip() for c in raw.split(",")]
    cols = [ident(c, "column") for c in raw if str(c).strip()]
    if not cols:
        raise AdminValidationError("no columns given")
    return cols


def password(params: dict[str, Any]) -> str:
    pw = params.get("password")
    if not isinstance(pw, str) or len(pw) < 8:
        raise AdminValidationError("password must be at least 8 characters")
    if "\x00" in pw:
        raise AdminValidationError("password contains invalid characters")
    return pw


def sql_string(value: str) -> str:
    """Standard SQL string literal (single quotes doubled). Only used for values that drivers
    cannot bind in DDL (passwords in CREATE USER on some engines); never logged."""
    return "'" + value.replace("'", "''") + "'"
