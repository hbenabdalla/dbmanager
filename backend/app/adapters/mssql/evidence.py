"""SQL Server evidence for the security and performance rule engines."""

from __future__ import annotations

from typing import Any

from app.adapters.base import AdapterError
from app.adapters.dbapi import jsonable, num, text
from app.adapters.mssql import sql as Q
from app.adapters.mssql.base import VERSIONS, SQLServerBase

MAX_DATABASES_SCANNED = 30


class SQLServerEvidenceMixin(SQLServerBase):
    async def _user_databases(self) -> list[Any]:
        dbs = await self.list_databases()  # type: ignore[attr-defined]
        return [d for d in dbs if not d.is_system and d.accessible][:MAX_DATABASES_SCANNED]

    async def get_security_context(self) -> dict[str, Any]:
        info = await self.get_server_info()
        logins = await self._logins()  # type: ignore[attr-defined]
        cfg = {text(r["name"]): int(r["value_in_use"] or 0) for r in await self._query(Q.SECURITY_CONFIG)}
        sa = await self._one(Q.SA_LOGIN)
        audits = await self._try(Q.SERVER_AUDITS)
        force = await self._try_one(Q.FORCE_ENCRYPTION)
        no_policy = await self._try(Q.LOGINS_NO_POLICY)
        dbs = await self.list_databases()  # type: ignore[attr-defined]
        guest_dbs, orphans, direct, scanned, errors = [], {}, [], 0, []
        for d in await self._user_databases():
            try:
                g = await self._one(Q.GUEST_CONNECT, database=d.name)
                if g and int(g["n"] or 0) > 0:
                    guest_dbs.append(d.name)
                o = await self._query(Q.ORPHANED_USERS, database=d.name)
                if o:
                    orphans[d.name] = [text(r["name"]) for r in o]
                for r in await self._query(Q.DIRECT_GRANT_COUNTS, database=d.name):
                    direct.append({"grantee": text(r["grantee"]), "grants": int(r["grants"] or 0), "database": d.name})
                scanned += 1
            except AdapterError as exc:
                errors.append(f"{d.name}: {exc}")
        sysadmins = [u.name for u in logins if "sysadmin" in u.member_of and not u.is_system]
        major = int((info.version_num or 0) // 1_000_000)
        return {
            "engine": self.engine,
            "version": info.version,
            "version_num": info.version_num,
            "product_name": VERSIONS.get(major, ("?", None))[0],
            "end_of_support": VERSIONS.get(major, ("?", None))[1],
            "cloud_provider": info.cloud_provider,
            "current_user": info.current_user,
            "current_user_superuser": info.extra.get("is_sysadmin"),
            "roles": [u.model_dump(mode="json") for u in logins],
            "sa_enabled": bool(sa) and not bool(sa["is_disabled"]),
            "sa_renamed": bool(sa) and text(sa["name"]) != "sa",
            "xp_cmdshell": bool(cfg.get("xp_cmdshell")),
            "clr_enabled": bool(cfg.get("clr enabled")),
            "clr_strict_security": cfg.get("clr strict security"),
            "ad_hoc_distributed_queries": bool(cfg.get("Ad Hoc Distributed Queries")),
            "ole_automation": bool(cfg.get("Ole Automation Procedures")),
            "cross_db_ownership_chaining": bool(cfg.get("cross db ownership chaining")),
            "scan_for_startup_procs": bool(cfg.get("scan for startup procs")),
            "sysadmin_members": sysadmins,
            "mixed_mode_auth": info.extra.get("mixed_mode_auth"),
            "force_encryption": bool(force["force_encryption"]) if force else None,
            "server_audits": len([a for a in audits or [] if a["is_state_enabled"]]) if audits is not None else None,
            "logins_without_policy": [text(r["name"]) for r in no_policy or []] if no_policy is not None else None,
            "trustworthy_dbs": [d.name for d in dbs if d.extra.get("trustworthy") and d.name != "msdb"],
            "guest_enabled_dbs": guest_dbs,
            "orphaned_users": orphans,
            "unencrypted_tde_dbs": [d.name for d in dbs if not d.is_system and not d.extra.get("encrypted_tde")],
            "direct_table_grants": direct,
            "databases_scanned": scanned,
            "scan_errors": errors[:10],
        }

    async def get_tuning_context(self) -> dict[str, Any]:
        info = await self.get_server_info()
        cfg = {
            text(r["name"]): num(r["value_in_use"])
            for r in await self._query(
                Q.CONFIG_VALUES.format(
                    names="'max server memory (MB)', 'max degree of parallelism', 'cost threshold for parallelism', "
                    "'optimize for ad hoc workloads'"
                )
            )
        }
        waits = await self._try(Q.TOP_WAITS) or []
        counters = await self._try(Q.PERF_COUNTERS) or []
        ple = next(
            (num(r["cntr_value"]) for r in counters if (text(r["counter_name"]) or "") == "Page life expectancy"), None
        )
        tempdb = await self._try_one(Q.TEMPDB_FILES) if not info.cloud_provider else None
        dbs = await self.list_databases()  # type: ignore[attr-defined]
        user_dbs = [d for d in dbs if not d.is_system]
        qs_flags = [d.extra.get("query_store") for d in user_dbs if d.extra.get("query_store") is not None]
        db = self.spec.database
        return {
            "engine": self.engine,
            "version_num": info.version_num,
            "max_server_memory_mb": int(cfg["max server memory (MB)"])
            if cfg.get("max server memory (MB)") is not None
            else None,
            "physical_memory_mb": int((info.extra.get("physical_memory_bytes") or 0) / 1024 / 1024) or None,
            "cpu_count": info.extra.get("cpu_count"),
            "maxdop": cfg.get("max degree of parallelism"),
            "cost_threshold_for_parallelism": cfg.get("cost threshold for parallelism"),
            "optimize_for_ad_hoc_workloads": cfg.get("optimize for ad hoc workloads"),
            "top_waits": [
                {
                    "wait_type": text(w["wait_type"]),
                    "pct": round(num(w["pct"], 0) or 0, 2),
                    "wait_time_ms": w["wait_time_ms"],
                }
                for w in waits
            ],
            "page_life_expectancy": ple,
            "missing_indexes": [jsonable(dict(r)) for r in await self._try(Q.MISSING_INDEXES) or []],
            "tempdb_data_files": int(tempdb["n"]) if tempdb else None,
            "query_store_enabled": any(qs_flags) if qs_flags else None,
            "auto_shrink_dbs": [d.name for d in user_dbs if d.extra.get("auto_shrink")],
            "auto_close_dbs": [d.name for d in user_dbs if d.extra.get("auto_close")],
            "log_backup_waits": [d.name for d in user_dbs if d.extra.get("log_reuse_wait") == "LOG_BACKUP"],
            "database": db,
            "unused_indexes": [jsonable(dict(r)) for r in await self._try(Q.UNUSED_INDEXES, database=db) or []]
            if db
            else [],
            "stale_statistics": [jsonable(dict(r)) for r in await self._try(Q.STALE_STATISTICS, database=db) or []]
            if db
            else [],
            "uptime_seconds": info.uptime_seconds,
        }
