"""MySQL-family evidence for the security and performance rule engines."""

from __future__ import annotations

from typing import Any

from app.adapters.dbapi import jsonable, num, text
from app.adapters.mysql import sql as Q
from app.adapters.mysql.base import MySQLFamilyBase, normalize_grantee

SECURITY_VARIABLES = [
    "require_secure_transport",
    "local_infile",
    "have_ssl",
    "tls_version",
    "ssl_cert",
    "skip_name_resolve",
    "secure_file_priv",
    "default_authentication_plugin",
    "authentication_policy",
    "sql_mode",
    "log_bin",
    "general_log",
    "old_passwords",
    "skip_grant_tables",
    "automatic_sp_privileges",
    "password_lifetime",
    "default_password_lifetime",
]
TUNING_VARIABLES = [
    "innodb_buffer_pool_size",
    "innodb_buffer_pool_instances",
    "innodb_log_file_size",
    "innodb_redo_log_capacity",
    "innodb_flush_log_at_trx_commit",
    "sync_binlog",
    "max_connections",
    "tmp_table_size",
    "max_heap_table_size",
    "table_open_cache",
    "thread_cache_size",
    "slow_query_log",
    "long_query_time",
    "performance_schema",
    "query_cache_type",
    "query_cache_size",
    "innodb_file_per_table",
    "sort_buffer_size",
    "join_buffer_size",
]


class MySQLEvidenceMixin(MySQLFamilyBase):
    async def _password_validation(self) -> bool | None:
        return None

    async def _audit_plugin_active(self, plugins: dict[str, str]) -> bool:
        return False

    async def _redundant_indexes(self) -> list[dict[str, Any]] | None:
        return None

    async def get_security_context(self) -> dict[str, Any]:
        info = await self.get_server_info()
        variables = await self._variables(SECURITY_VARIABLES)
        accounts = await self._all_accounts()  # type: ignore[attr-defined]
        plugins = {text(r["name"]) or "": text(r["status"]) or "" for r in await self._try(Q.PLUGINS) or []}
        anon = [u.name for u in accounts if u.attributes.get("anonymous")]
        wildcard_admins = [
            u.name
            for u in accounts
            if u.attributes.get("wildcard_host") and (u.is_superuser or u.admin_reasons) and not u.is_system
        ]
        no_password = [
            u.name
            for u in accounts
            if u.can_login
            and u.attributes.get("has_password") is False
            and (u.attributes.get("auth_plugin") or "")
            in ("", "mysql_native_password", "caching_sha2_password", "sha256_password")
        ]
        # mysql_native_password is deprecated in MySQL 8.0.34 and removed in 9.0; it remains the
        # standard (not deprecated) plugin in MariaDB
        native = (
            [
                u.name
                for u in accounts
                if u.can_login and u.attributes.get("auth_plugin") == "mysql_native_password" and not u.is_system
            ]
            if self.flavor == "mysql"
            else []
        )
        file_holders = sorted({normalize_grantee(r["grantee"]) for r in await self._try(Q.FILE_PRIV_HOLDERS) or []})
        direct = [
            {"grantee": normalize_grantee(r["grantee"]), "grants": int(r["grants"] or 0)}
            for r in await self._try(Q.DIRECT_GRANT_COUNTS) or []
        ]
        return {
            "engine": self.engine,
            "flavor": self.flavor,
            "version": info.version,
            "version_num": info.version_num,
            "cloud_provider": info.cloud_provider,
            "current_user": info.current_user,
            "current_user_superuser": any(u.name == info.current_user and u.is_superuser for u in accounts),
            "settings": {k: text(v) for k, v in variables.items()},
            "roles": [u.model_dump(mode="json") for u in accounts],
            "grant_tables_readable": not any(u.attributes.get("source") for u in accounts),
            "anonymous_accounts": anon,
            "wildcard_host_admins": wildcard_admins,
            "accounts_without_password": no_password,
            "native_password_accounts": native,
            "file_privilege_holders": [g for g in file_holders if g.split("@")[0] not in ("root", "mysql.session")],
            "require_secure_transport": _onoff(variables.get("require_secure_transport")),
            "local_infile": _onoff(variables.get("local_infile")),
            "secure_file_priv": text(variables.get("secure_file_priv")),
            "skip_name_resolve": _onoff(variables.get("skip_name_resolve")),
            "tls_configured": bool(text(variables.get("ssl_cert")))
            or (text(variables.get("have_ssl")) or "").upper() == "YES",
            "default_authentication_plugin": text(
                variables.get("default_authentication_plugin") or variables.get("authentication_policy")
            ),
            "validate_password_component": await self._password_validation(),
            "audit_plugin_active": await self._audit_plugin_active(plugins),
            "direct_table_grants": direct,
            "plugins": {k: v for k, v in plugins.items() if v == "ACTIVE"},
        }

    async def get_tuning_context(self) -> dict[str, Any]:
        info = await self._server()
        variables = await self._variables(TUNING_VARIABLES)
        st = await self._status()
        reqs, reads = num(st.get("innodb_buffer_pool_read_requests")), num(st.get("innodb_buffer_pool_reads"))
        hit = round(100.0 * (reqs - reads) / reqs, 2) if reqs and reads is not None and reqs > 0 else None
        tmp, tmp_disk = num(st.get("created_tmp_tables"), 0) or 0, num(st.get("created_tmp_disk_tables"), 0) or 0
        host_gb = num((self.spec.options or {}).get("host_memory_gb"))
        uptime = num(st.get("uptime"), 0) or 0
        ps = bool(info.get("performance_schema"))
        unused = (
            [jsonable(dict(r)) for r in await self._try(Q.UNUSED_INDEXES) or []] if ps and uptime > 7 * 86400 else []
        )
        return {
            "engine": self.engine,
            "flavor": self.flavor,
            "version_num": info["version_num"],
            "uptime_seconds": uptime,
            "settings": {k: text(v) for k, v in variables.items()},
            "innodb_buffer_pool_size": num(variables.get("innodb_buffer_pool_size")),
            "ram_bytes": host_gb * 1024**3 if host_gb else None,
            "innodb_buffer_pool_hit_ratio": hit,
            "tmp_disk_tables_pct": round(100.0 * tmp_disk / tmp, 1) if tmp > 100 else 0.0,
            "slow_query_log": (text(variables.get("slow_query_log")) or "").upper() or None,
            "max_connections": num(variables.get("max_connections")),
            "max_used_connections": num(st.get("max_used_connections")),
            "performance_schema": ps,
            "query_cache_enabled": (text(variables.get("query_cache_type")) or "OFF").upper() not in ("OFF", "0")
            and (num(variables.get("query_cache_size"), 0) or 0) > 0,
            "innodb_flush_log_at_trx_commit": text(variables.get("innodb_flush_log_at_trx_commit")),
            "tables_without_pk": [text(r["name"]) for r in await self._try(Q.TABLES_WITHOUT_PK) or []],
            "unused_indexes": unused,
            "unused_index_evidence_days": round(uptime / 86400, 1),
            "redundant_indexes": await self._redundant_indexes() or [],
            "full_scan_statements": [jsonable(dict(r)) for r in await self._try(Q.FULL_SCAN_STATEMENTS) or []]
            if ps
            else [],
            "fragmented_tables": [jsonable(dict(r)) for r in await self._try(Q.FRAGMENTED_TABLES) or []],
        }


def _onoff(v: Any) -> str | None:
    s = (text(v) or "").upper()
    if s in ("1", "ON", "YES", "TRUE"):
        return "ON"
    if s in ("0", "OFF", "NO", "FALSE"):
        return "OFF"
    return s or None
