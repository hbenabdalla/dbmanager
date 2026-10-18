"""Oracle evidence for the security and performance rule engines (no management-pack views)."""

from __future__ import annotations

from typing import Any

from app.adapters.dbapi import jsonable, num, text
from app.adapters.oracle import sql as Q
from app.adapters.oracle.base import ENDED_SUPPORT, OracleBase

SECURITY_PARAMS = (
    "'audit_trail', 'audit_sys_operations', 'remote_login_passwordfile', 'sql92_security', "
    "'sec_return_server_release_banner', 'resource_limit', 'os_authent_prefix', 'remote_os_authent', "
    "'global_names', 'sec_protocol_error_further_action'"
)
TUNING_PARAMS = (
    "'pga_aggregate_target', 'pga_aggregate_limit', 'sga_target', 'memory_target', 'cursor_sharing', "
    "'optimizer_mode', 'statistics_level', 'control_management_pack_access', 'processes', 'sessions', "
    "'open_cursors', 'db_cache_size'"
)


def _effective_limits(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Resolve DEFAULT limits of a profile resource to the DEFAULT profile's value."""
    vals = {text(r["profile"]) or "": (text(r["limit"]) or "").upper() for r in rows}
    base = vals.get("DEFAULT", "UNLIMITED")
    return {p: (base if v == "DEFAULT" else v) for p, v in vals.items()}


class OracleEvidenceMixin(OracleBase):
    async def _params(self, names: str) -> dict[str, str | None]:
        return {
            text(r["name"]) or "": text(r["value"])
            for r in await self._try(Q.PARAMETER_VALUES.format(names=names)) or []
        }

    async def get_security_context(self) -> dict[str, Any]:
        info = await self._server()
        params = await self._params(SECURITY_PARAMS)
        maintained_open = await self._try(Q.OPEN_MAINTAINED)
        samples = await self._try(Q.SAMPLE_SCHEMAS_OPEN) or []
        defpwd = await self._try(Q.DEFAULT_PWD_USERS)
        public_exec = await self._try(Q.PUBLIC_EXECUTE)
        pure = await self._try_one(Q.UNIFIED_PURE)
        policies = await self._try(Q.UNIFIED_POLICIES)
        life = _effective_limits(await self._try(Q.PASSWORD_LIFE) or [])
        failed = _effective_limits(await self._try(Q.FAILED_LOGIN) or [])
        used_profiles = {text(r["profile"]) or "": int(r["n"] or 0) for r in await self._try(Q.PROFILE_USERS) or []}
        dba = await self._try(Q.DBA_GRANTEES)
        any_privs = await self._try(Q.ANY_PRIV_GRANTEES)
        weak = await self._try(Q.WEAK_PASSWORD_VERSIONS)
        public_sys = await self._try(Q.PUBLIC_SYS_PRIVS)
        tde = await self._try(Q.TDE_TABLESPACES)
        direct = await self._try(Q.DIRECT_TABLE_GRANTS) or []
        users = await self.list_users()  # type: ignore[attr-defined]
        protocol = (text(info.get("network_protocol")) or "").lower()
        open_default = sorted(
            {text(r["username"]) or "" for r in maintained_open or []}
            | {text(r["username"]) or "" for r in samples}
            | ({text(r["username"]) or "" for r in defpwd or []} & {u.name for u in users if u.can_login})
        )
        any_by_grantee: dict[str, list[str]] = {}
        for r in any_privs or []:
            any_by_grantee.setdefault(text(r["grantee"]) or "", []).append(text(r["privilege"]) or "")
        major = int(info["major"])
        return {
            "engine": self.engine,
            "version": info.get("version_full") or text(info.get("version")),
            "version_num": info["version_num"],
            "major_version": major,
            "end_of_support": ENDED_SUPPORT.get(major),
            "edition": self._edition(info.get("banner")),
            "container": info["con_name"],
            "cdb": info["is_cdb"],
            "current_user": text(info.get("session_user")),
            "roles": [u.model_dump(mode="json") for u in users],
            "default_accounts_open": open_default if maintained_open is not None else None,
            "default_password_accounts": sorted(text(r["username"]) or "" for r in defpwd)
            if defpwd is not None
            else None,
            "public_execute_packages": [text(r["table_name"]) for r in public_exec]
            if public_exec is not None
            else None,
            "audit_trail": (params.get("audit_trail") or "").upper() or None,
            "audit_sys_operations": (params.get("audit_sys_operations") or "").upper() or None,
            "unified_auditing_pure": (text(pure["value"]) == "TRUE") if pure else None,
            "unified_policies_enabled": sorted(text(r["policy_name"]) or "" for r in policies)
            if policies is not None
            else None,
            "unified_auditing": ((text(pure["value"]) == "TRUE") if pure else False) or bool(policies),
            "unlimited_password_profiles": sorted(
                p for p, v in life.items() if v == "UNLIMITED" and used_profiles.get(p)
            ),
            "unlimited_failed_login_profiles": sorted(
                p for p, v in failed.items() if v == "UNLIMITED" and used_profiles.get(p)
            ),
            # Thin mode cannot negotiate Native Network Encryption: a successful plain TCP
            # connection proves SQLNET.ENCRYPTION_SERVER is not REQUIRED on this listener.
            "network_encryption": "TCPS" if protocol == "tcps" else ("NOT_REQUIRED" if protocol == "tcp" else None),
            "monitoring_session_protocol": protocol or None,
            "dba_role_grantees": sorted(text(r["grantee"]) or "" for r in dba) if dba is not None else None,
            "any_privilege_grantees": any_by_grantee if any_privs is not None else None,
            "weak_password_versions": [jsonable(dict(r)) for r in weak] if weak is not None else None,
            "public_system_privileges": sorted(text(r["privilege"]) or "" for r in public_sys)
            if public_sys is not None
            else None,
            "unencrypted_tablespaces": [text(r["tablespace_name"]) for r in tde or [] if text(r["encrypted"]) != "YES"]
            if tde is not None
            else None,
            "remote_login_passwordfile": params.get("remote_login_passwordfile"),
            "sql92_security": params.get("sql92_security"),
            "remote_os_authent": params.get("remote_os_authent"),
            "direct_table_grants": [{"grantee": text(r["grantee"]), "grants": int(r["grants"] or 0)} for r in direct],
            "log_mode": text(info.get("log_mode")),
        }

    async def get_tuning_context(self) -> dict[str, Any]:
        info = await self._server()
        params = await self._params(TUNING_PARAMS)
        waits = await self._try(Q.WAIT_CLASSES) or []
        events = await self._try(Q.TOP_EVENTS) or []
        stats = {text(r["name"]): num(r["value"], 0) or 0.0 for r in await self._try(Q.SYSSTAT) or []}
        pga = {text(r["name"]): num(r["value"]) for r in await self._try(Q.PGASTAT) or []}
        ts = await self._try(Q.TABLESPACES) or []
        switches = await self._try_one(Q.LOG_SWITCHES_24H)
        redo = await self._try(Q.REDO_LOGS) or []
        fra = await self._try_one(Q.FRA)
        logical = stats.get("db block gets", 0.0) + stats.get("consistent gets", 0.0)
        hit = round(100.0 * (1 - stats.get("physical reads", 0.0) / logical), 2) if logical else None
        parses, hard = stats.get("parse count (total)"), stats.get("parse count (hard)")
        executes = stats.get("execute count")
        return {
            "engine": self.engine,
            "version_num": info["version_num"],
            "edition": self._edition(info.get("banner")),
            "diagnostics_pack_licensed": self.diagnostics_pack,
            "tuning_pack_licensed": self.tuning_pack,
            "control_management_pack_access": params.get("control_management_pack_access"),
            "tablespaces": [
                {
                    "name": text(t["name"]),
                    "contents": text(t["contents"]),
                    "used_pct": round(num(t["used_percent"], 0) or 0, 2),
                    "autoextend": bool(t["autoextend"]),
                    "used_bytes": jsonable(t["used_bytes"]),
                    "max_bytes": jsonable(t["max_bytes"]),
                }
                for t in ts
                if text(t["contents"]) != "TEMPORARY"
            ],
            "top_waits": [
                {
                    "wait_class": text(w["wait_class"]),
                    "pct": round(num(w["pct"], 0) or 0, 2),
                    "time_waited_cs": jsonable(w["time_waited"]),
                }
                for w in waits
            ],
            "top_events": [jsonable(dict(e)) for e in events],
            "buffer_cache_hit_ratio": hit,
            "pga_over_allocation_count": int(pga.get("over allocation count") or 0),
            "pga_aggregate_target_bytes": int(num(params.get("pga_aggregate_target"), 0) or 0) or None,
            "pga_cache_hit_percentage": pga.get("cache hit percentage"),
            "hard_parse_ratio": round(100.0 * hard / parses, 2) if parses and hard is not None else None,
            "hard_parses": hard,
            "execute_count": executes,
            "cursor_sharing": params.get("cursor_sharing"),
            "statistics_level": params.get("statistics_level"),
            "log_switches_24h": int(switches["n"]) if switches else None,
            "redo_log_bytes_min": min((int(num(r["bytes"], 0) or 0) for r in redo), default=None),
            "fra_used_percent": round(
                100.0
                * ((num(fra["space_used"], 0) or 0) - (num(fra["space_reclaimable"], 0) or 0))
                / (num(fra["space_limit"]) or 1),
                2,
            )
            if fra and num(fra["space_limit"])
            else None,
            "stale_stats_tables": [jsonable(dict(r)) for r in await self._try(Q.STALE_STATS) or []],
            "invalid_objects": [jsonable(dict(r)) for r in await self._try(Q.INVALID_OBJECTS) or []],
            "unusable_indexes": [jsonable(dict(r)) for r in await self._try(Q.UNUSABLE_INDEXES) or []],
            "cpu_count": int(info["osstat"].get("NUM_CPUS") or 0) or None,
            "log_mode": text(info.get("log_mode")),
            "uptime_seconds": None,
        }
