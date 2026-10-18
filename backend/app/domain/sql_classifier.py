"""Parser-based SQL statement classification for the SQL workspace and EXPLAIN.

The classifier is deliberately *fail-safe*: anything it cannot positively identify as
read-only is treated as at least ``PRIVILEGED_CHANGE``. It is one layer of defense; the
PostgreSQL adapter additionally runs read-only statements inside ``BEGIN READ ONLY`` so
the database itself rejects writes the classifier might miss.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from app.domain.risk import RiskClass, max_risk

logging.getLogger("sqlglot").setLevel(logging.ERROR)

ENGINE_DIALECT = {
    "postgresql": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "mssql": "tsql",
    "oracle": "oracle",
    "db2": "postgres",  # sqlglot has no Db2 dialect; ANSI-ish parsing + keyword checks
    "db2zos": "postgres",
}

# Functions that have side effects even inside SELECT (PostgreSQL + common vendors).
SIDE_EFFECT_FUNCTIONS = {
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "pg_rotate_logfile",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "lo_unlink",
    "set_config",
    "pg_advisory_lock",
    "pg_advisory_xact_lock",
    "dblink",
    "dblink_exec",
    "pg_create_restore_point",
    "pg_switch_wal",
    "pg_promote",
    "pg_drop_replication_slot",
    "pg_create_physical_replication_slot",
    "pg_create_logical_replication_slot",
    "nextval",
    "setval",
    "pg_sleep",
    "txid_current",
    "pg_logical_emit_message",
    "pg_backup_start",
    "pg_backup_stop",
    "sys_exec",
    "xp_cmdshell",
    "load_file",
    "sleep",
    "benchmark",
    "dbms_lock.sleep",
}

COMMAND_RULES: dict[str, tuple[str, RiskClass]] = {
    "EXPLAIN": ("EXPLAIN", RiskClass.READ),  # refined below (ANALYZE executes)
    "SHOW": ("SHOW", RiskClass.READ),
    "DESCRIBE": ("DESCRIBE", RiskClass.READ),
    "DESC": ("DESCRIBE", RiskClass.READ),
    "VACUUM": ("MAINTENANCE", RiskClass.LOW_RISK_CHANGE),
    "ANALYZE": ("MAINTENANCE", RiskClass.LOW_RISK_CHANGE),
    "REINDEX": ("MAINTENANCE", RiskClass.PRIVILEGED_CHANGE),
    "CLUSTER": ("MAINTENANCE", RiskClass.PRIVILEGED_CHANGE),
    "REFRESH": ("MAINTENANCE", RiskClass.LOW_RISK_CHANGE),
    "CHECKPOINT": ("MAINTENANCE", RiskClass.PRIVILEGED_CHANGE),
    "LOCK": ("LOCK", RiskClass.PRIVILEGED_CHANGE),
    "CALL": ("CALL", RiskClass.PRIVILEGED_CHANGE),
    "EXEC": ("CALL", RiskClass.PRIVILEGED_CHANGE),
    "EXECUTE": ("CALL", RiskClass.PRIVILEGED_CHANGE),
    "DO": ("ANONYMOUS_BLOCK", RiskClass.PRIVILEGED_CHANGE),
    "BEGIN": ("ANONYMOUS_BLOCK", RiskClass.PRIVILEGED_CHANGE),
    "DECLARE": ("ANONYMOUS_BLOCK", RiskClass.PRIVILEGED_CHANGE),
    "CREATE": ("DDL", RiskClass.PRIVILEGED_CHANGE),
    "ALTER": ("DDL", RiskClass.PRIVILEGED_CHANGE),
    "DROP": ("DDL", RiskClass.DESTRUCTIVE),
    "TRUNCATE": ("DDL", RiskClass.DESTRUCTIVE),
    "GRANT": ("DCL", RiskClass.PRIVILEGED_CHANGE),
    "REVOKE": ("DCL", RiskClass.DESTRUCTIVE),
    "COPY": ("COPY", RiskClass.PRIVILEGED_CHANGE),
    "SET": ("SESSION", RiskClass.LOW_RISK_CHANGE),
    "RESET": ("SESSION", RiskClass.LOW_RISK_CHANGE),
    "LISTEN": ("SESSION", RiskClass.LOW_RISK_CHANGE),
    "SHUTDOWN": ("ADMIN", RiskClass.DESTRUCTIVE),
    "KILL": ("ADMIN", RiskClass.PRIVILEGED_CHANGE),
    "RUNSTATS": ("MAINTENANCE", RiskClass.LOW_RISK_CHANGE),
    "REORG": ("MAINTENANCE", RiskClass.PRIVILEGED_CHANGE),
    # SQL Server / Db2 native backup and restore: a backup writes files on the database host,
    # a restore replaces a database
    "BACKUP": ("BACKUP", RiskClass.PRIVILEGED_CHANGE),
    "RESTORE": ("RESTORE", RiskClass.DESTRUCTIVE),
}

PRIVILEGE_DDL = re.compile(r"^\s*(CREATE|ALTER|DROP)\s+(ROLE|USER|GROUP|LOGIN)\b", re.I)
ALTER_SYSTEM = re.compile(r"^\s*ALTER\s+(SYSTEM|DATABASE|SERVER\s+CONFIGURATION|INSTANCE)\b", re.I)


@dataclass
class Classification:
    statement_type: str
    risk: RiskClass
    read_only: bool
    statement_count: int
    reasons: list[str] = field(default_factory=list)
    required_permissions: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    parse_ok: bool = True
    explain_analyze: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "statement_type": self.statement_type,
            "risk": self.risk.value,
            "read_only": self.read_only,
            "statement_count": self.statement_count,
            "reasons": self.reasons,
            "required_permissions": self.required_permissions,
            "objects": self.objects,
            "parse_ok": self.parse_ok,
            "explain_analyze": self.explain_analyze,
        }


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql).strip()


def _first_keyword(sql: str) -> str:
    m = re.match(r"\s*([A-Za-z]+)", _strip_comments(sql))
    return m.group(1).upper() if m else ""


def _function_names(tree: exp.Expression) -> set[str]:
    names: set[str] = set()
    for fn in tree.find_all(exp.Func):
        if isinstance(fn, exp.Anonymous):
            names.add(str(fn.name).lower())
        else:
            names.add(fn.sql_name().lower())
            if fn.key:
                names.add(fn.key.lower())
    return names


def _tables(tree: exp.Expression) -> list[str]:
    out: list[str] = []
    for t in tree.find_all(exp.Table):
        name = ".".join(p for p in (t.args.get("db") and t.db, t.name) if p)
        if name and name not in out:
            out.append(name)
    return out[:20]


def _permissions_for(stype: str, risk: RiskClass) -> list[str]:
    if risk is RiskClass.READ:
        return ["query.execute"]
    perms = ["query.modify"]
    if stype == "DCL" or stype == "ROLE_DDL":
        perms.append("privilege.manage")
    if stype in ("DDL", "ROLE_DDL"):
        perms.append("schema.modify")
    if stype == "SYSTEM_CONFIG":
        perms.append("performance.tune")
    return perms


def classify_sql(sql: str, engine: str = "postgresql") -> Classification:
    dialect = ENGINE_DIALECT.get(engine, "postgres")
    cleaned = _strip_comments(sql)
    if not cleaned:
        return Classification("EMPTY", RiskClass.READ, True, 0, ["empty statement"], [], parse_ok=True)

    try:
        trees = [t for t in sqlglot.parse(sql, read=dialect) if t is not None]
        parse_ok = True
    except Exception:  # noqa: BLE001 - parse failure => fail safe
        trees = []
        parse_ok = False

    count = len(trees) if parse_ok else max(1, len([s for s in cleaned.split(";") if s.strip()]))
    if count > 1:
        c = Classification(
            "MULTIPLE", RiskClass.PRIVILEGED_CHANGE, False, count, ["multiple statements are not allowed"], []
        )
        c.required_permissions = ["query.modify"]
        return c

    if not parse_ok or not trees:
        kw = _first_keyword(sql)
        stype, risk = COMMAND_RULES.get(kw, ("UNKNOWN", RiskClass.PRIVILEGED_CHANGE))
        reasons = ["statement could not be fully parsed; classified by leading keyword (fail-safe)"]
        return _finish(Classification(stype, risk, False, 1, reasons, parse_ok=False), sql)

    tree = trees[0]
    reasons: list[str] = []
    risk = RiskClass.READ
    stype = "SELECT"

    if isinstance(tree, exp.Command):
        kw = str(tree.this).upper()
        stype, risk = COMMAND_RULES.get(kw, ("UNKNOWN", RiskClass.PRIVILEGED_CHANGE))
        if kw == "EXPLAIN":
            rest = tree.expression.name if tree.expression is not None else ""
            inner_sql = re.sub(r"^\s*\((.*?)\)", "", rest, flags=re.S) if rest.lstrip().startswith("(") else rest
            options = rest[: len(rest) - len(inner_sql)].upper()
            if re.search(r"\bANALY[SZ]E\b", options) or re.match(r"\s*ANALY[SZ]E\b", rest, re.I):
                inner = classify_sql(re.sub(r"^\s*ANALY[SZ]E\s+(VERBOSE\s+)?", "", inner_sql, flags=re.I), engine)
                c = Classification(
                    "EXPLAIN_ANALYZE",
                    max_risk(RiskClass.LOW_RISK_CHANGE, inner.risk),
                    False,
                    1,
                    ["EXPLAIN ANALYZE executes the statement", *inner.reasons],
                    explain_analyze=True,
                )
                c.objects = inner.objects
                extra = ["query.explain_analyze"] + ([] if inner.read_only else ["query.modify"])
                return _finish(c, sql, extra_perms=extra)
            inner = classify_sql(inner_sql, engine)
            c = Classification("EXPLAIN", RiskClass.READ, True, 1, ["estimated plan only; statement is not executed"])
            c.objects = inner.objects
            return _finish(c, sql, extra_perms=["query.explain"])
        if PRIVILEGE_DDL.match(cleaned):
            stype = "ROLE_DDL"
            risk = RiskClass.DESTRUCTIVE if kw == "DROP" else RiskClass.PRIVILEGED_CHANGE
        elif ALTER_SYSTEM.match(cleaned):
            stype, risk = "SYSTEM_CONFIG", RiskClass.DESTRUCTIVE
            reasons.append("changes server-wide configuration")
        if kw == "COPY" and re.search(r"\bPROGRAM\b", cleaned, re.I):
            risk = RiskClass.DESTRUCTIVE
            reasons.append("COPY ... PROGRAM executes an operating system command")
        reasons.append(f"{kw} statement")
        return _finish(Classification(stype, risk, risk is RiskClass.READ, 1, reasons), sql)

    if isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery, exp.Values)):
        stype = "SELECT"
        modifying = [n for n in tree.find_all(exp.Insert, exp.Update, exp.Delete, exp.Merge)]
        if modifying:
            risk = RiskClass.LOW_RISK_CHANGE
            stype = "DML"
            reasons.append("data-modifying statement inside WITH clause")
            for n in modifying:
                if isinstance(n, (exp.Update, exp.Delete)) and not n.args.get("where"):
                    risk = RiskClass.DESTRUCTIVE
                    reasons.append(f"{n.key.upper()} without WHERE affects every row")
        if tree.find(exp.Into):
            risk = max_risk(risk, RiskClass.PRIVILEGED_CHANGE)
            stype = "DDL"
            reasons.append("SELECT INTO creates a table")
        if tree.args.get("locks"):
            risk = max_risk(risk, RiskClass.LOW_RISK_CHANGE)
            reasons.append("row locking clause (FOR UPDATE/SHARE)")
        fns = _function_names(tree) & SIDE_EFFECT_FUNCTIONS
        if fns:
            risk = max_risk(risk, RiskClass.PRIVILEGED_CHANGE)
            reasons.append("calls functions with side effects: " + ", ".join(sorted(fns)))
        if risk is RiskClass.READ:
            reasons.append("read-only query")
    elif isinstance(tree, exp.Insert):
        stype, risk = "DML", RiskClass.LOW_RISK_CHANGE
        reasons.append("inserts rows")
    elif isinstance(tree, exp.Update):
        stype, risk = "DML", RiskClass.LOW_RISK_CHANGE
        reasons.append("updates rows")
        if not tree.args.get("where"):
            risk = RiskClass.DESTRUCTIVE
            reasons.append("UPDATE without WHERE affects every row")
    elif isinstance(tree, exp.Delete):
        stype, risk = "DML", RiskClass.LOW_RISK_CHANGE
        reasons.append("deletes rows")
        if not tree.args.get("where"):
            risk = RiskClass.DESTRUCTIVE
            reasons.append("DELETE without WHERE removes every row")
    elif isinstance(tree, exp.Merge):
        stype, risk = "DML", RiskClass.PRIVILEGED_CHANGE
        reasons.append("MERGE may insert, update and delete rows")
    elif isinstance(tree, exp.TruncateTable):
        stype, risk = "DDL", RiskClass.DESTRUCTIVE
        reasons.append("TRUNCATE removes all rows and cannot be filtered")
    elif isinstance(tree, exp.Drop):
        kind = str(tree.args.get("kind") or "").upper()
        stype = "ROLE_DDL" if kind in ("ROLE", "USER") else "DDL"
        risk = RiskClass.DESTRUCTIVE
        reasons.append(f"DROP {kind or 'object'} permanently removes the object")
        if kind == "DATABASE":
            reasons.append("dropping a database removes all of its data")
    elif isinstance(tree, exp.Create):
        kind = str(tree.args.get("kind") or "").upper()
        stype = "ROLE_DDL" if kind in ("ROLE", "USER") else "DDL"
        risk = RiskClass.PRIVILEGED_CHANGE
        reasons.append(f"CREATE {kind or 'object'}")
        if tree.args.get("replace"):
            risk = RiskClass.DESTRUCTIVE
            reasons.append("OR REPLACE overwrites an existing definition")
    elif isinstance(tree, exp.Alter):
        stype, risk = "DDL", RiskClass.PRIVILEGED_CHANGE
        reasons.append("alters an existing object")
        if any(isinstance(a, exp.Drop) for a in tree.args.get("actions") or []) or re.search(
            r"\bDROP\b", cleaned, re.I
        ):
            risk = RiskClass.DESTRUCTIVE
            reasons.append("ALTER ... DROP removes columns or constraints")
    elif isinstance(tree, exp.Grant):
        stype, risk = "DCL", RiskClass.PRIVILEGED_CHANGE
        reasons.append("grants privileges")
    elif isinstance(tree, exp.Revoke):
        stype, risk = "DCL", RiskClass.DESTRUCTIVE
        reasons.append("revokes privileges (may break applications)")
    elif isinstance(tree, exp.Analyze):
        stype, risk = "MAINTENANCE", RiskClass.LOW_RISK_CHANGE
        reasons.append("refreshes planner statistics")
    elif isinstance(tree, exp.Set):
        stype, risk = "SESSION", RiskClass.LOW_RISK_CHANGE
        reasons.append("changes session settings")
    elif isinstance(tree, exp.Copy):
        stype, risk = "COPY", RiskClass.PRIVILEGED_CHANGE
        reasons.append("COPY reads or writes server-side files")
        if re.search(r"\bPROGRAM\b", cleaned, re.I):
            risk = RiskClass.DESTRUCTIVE
            reasons.append("COPY ... PROGRAM executes an operating system command")
    elif isinstance(tree, exp.Comment):
        stype, risk = "DDL", RiskClass.LOW_RISK_CHANGE
        reasons.append("changes an object comment")
    elif isinstance(tree, (exp.Transaction, exp.Commit, exp.Rollback)):
        stype, risk = "TRANSACTION", RiskClass.PRIVILEGED_CHANGE
        reasons.append("explicit transaction control is managed by the workspace")
    else:
        stype, risk = "UNKNOWN", RiskClass.PRIVILEGED_CHANGE
        reasons.append(f"unrecognized statement type {type(tree).__name__} (fail-safe)")

    c = Classification(stype, risk, risk is RiskClass.READ, 1, reasons)
    c.objects = _tables(tree)
    return _finish(c, sql)


def _finish(c: Classification, sql: str, extra_perms: list[str] | None = None) -> Classification:
    perms = _permissions_for(c.statement_type, c.risk)
    if c.statement_type in ("EXPLAIN", "EXPLAIN_ANALYZE"):
        perms = []
    for p in extra_perms or []:
        if p not in perms:
            perms.append(p)
    c.required_permissions = perms
    return c
