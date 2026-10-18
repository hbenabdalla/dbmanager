"""Db2 LUW evidence for the security and performance rule engines."""

from __future__ import annotations

from typing import Any

from app.adapters.db2 import sql as Q
from app.adapters.db2.base import ENDED_SUPPORT, Db2Base
from app.adapters.dbapi import jsonable, num, text

DBM_SECURITY = "'authentication', 'srvcon_auth', 'ssl_svcename', 'svcename', 'sysadm_group', 'sysctrl_group', 'sysmaint_group', 'sysmon_group', 'audit_buf_sz', 'trust_allclnts'"
DB_SECURITY = "'restrict_access', 'discover_db'"
DB_TUNING = "'mon_act_metrics', 'mon_req_metrics', 'mon_obj_metrics', 'auto_runstats', 'auto_reorg', 'auto_maint', 'self_tuning_mem', 'sortheap', 'sheapthres_shr', 'pckcachesz', 'locklist', 'maxlocks', 'logarchmeth1', 'logprimary', 'logsecond', 'logfilsiz'"


class Db2EvidenceMixin(Db2Base):
    async def _cfg(self, sql: str, names: str) -> dict[str, str | None]:
        return {text(r["name"]) or "": text(r["value"]) for r in await self._try(sql.format(names=names)) or []}

    async def get_security_context(self) -> dict[str, Any]:
        info = await self._server()
        dbm = await self._cfg(Q.DBMCFG_VALUES, DBM_SECURITY)
        db = await self._cfg(Q.DBCFG_VALUES, DB_SECURITY)
        auths = await self._db_authorities()  # type: ignore[attr-defined]
        audit = await self._try_one(Q.AUDIT_USE)
        public_tab = await self._try_one(Q.PUBLIC_TABLE_SELECT)
        users = await self.list_users()  # type: ignore[attr-defined]

        def holders(authority: str) -> list[str]:
            return sorted(
                f"{g}" + ("" if t == "U" else f" ({'group' if t == 'G' else 'role'})")
                for (g, t), a in auths.items()
                if authority in a
            )

        public = sorted(set(auths.get(("PUBLIC", "G"), []) + auths.get(("PUBLIC", "U"), [])))
        return {
            "engine": self.engine,
            "version": info["version"],
            "version_num": info["version_num"],
            "end_of_support": ENDED_SUPPORT.get(int(info["major"] or 0)),
            "current_user": text(info.get("cur_user")),
            "session_authorities": info["authorities"],
            "roles": [u.model_dump(mode="json") for u in users],
            "public_grants": public,
            "dbadm_holders": holders("DBADM"),
            "secadm_holders": holders("SECADM"),
            "dataaccess_holders": holders("DATAACCESS"),
            "accessctrl_holders": holders("ACCESSCTRL"),
            "not_fenced_holders": holders("CREATE_NOT_FENCED_ROUTINE"),
            "audit_active": (int(audit["n"] or 0) > 0) if audit else None,
            "authentication": (dbm.get("authentication") or "").upper() or None,
            "srvcon_auth": (dbm.get("srvcon_auth") or "").upper() or None,
            "ssl_svcename": dbm.get("ssl_svcename") or None,
            "svcename": dbm.get("svcename"),
            "trust_allclnts": dbm.get("trust_allclnts"),
            "sysadm_group": dbm.get("sysadm_group"),
            "restrict_access": db.get("restrict_access"),
            "public_table_select_grants": int(public_tab["n"] or 0) if public_tab else None,
            "dbm_cfg_readable": bool(dbm),
        }

    async def get_tuning_context(self) -> dict[str, Any]:
        info = await self._server()
        cfg = await self._cfg(Q.DBCFG_VALUES, DB_TUNING)
        w = await self._try_one(Q.WORKLOAD_TOTALS) or {}
        bps = []
        for b in await self._try(Q.BUFFERPOOLS) or []:
            lr, pr = num(b["l_reads"], 0) or 0, num(b["p_reads"], 0) or 0
            if lr >= 10_000:
                bps.append(
                    {
                        "name": text(b["bp_name"]),
                        "hit_ratio": round(100.0 * (1 - pr / lr), 2),
                        "logical_reads": lr,
                        "physical_reads": pr,
                    }
                )
        sorts, overflows = num(w.get("total_sorts"), 0) or 0, num(w.get("sort_overflows"), 0) or 0
        reorg = []
        for r in await self._try(Q.REORG_CANDIDATES) or []:
            reorg.append(
                jsonable(
                    {
                        "table": f"{text(r['tabschema'])}.{text(r['tabname'])}",
                        "f1": r["f1"],
                        "f2": r["f2"],
                        "f3": r["f3"],
                        "card": r["card"],
                        "fpages": r["fpages"],
                        "source": "REORGCHK-style formulas from catalog statistics (F1 > 5, F2 < 70, F3 < 80)",
                    }
                )
            )
        runstats = [
            jsonable(
                {
                    "table": f"{text(r['tabschema'])}.{text(r['tabname'])}",
                    "stats_time": r["stats_time"],
                    "stats_time_days": int(r["stats_time_days"]),
                    "card": r["card"],
                }
            )
            for r in await self._try(Q.RUNSTATS_CANDIDATES) or []
        ]
        pending = [f"{text(r['tabschema'])}.{text(r['tabname'])}" for r in await self._try(Q.REORG_PENDING) or []]
        log = await self._try_one(Q.TRANSACTION_LOG)
        used, avail = (num(log["used"], 0) or 0, num(log["available"], 0) or 0) if log else (0, 0)
        return {
            "engine": self.engine,
            "version_num": info["version_num"],
            "bufferpools": bps,
            "sort_overflow_pct": round(100.0 * overflows / sorts, 2) if sorts else 0.0,
            "package_cache_overflows": None,  # PKG_CACHE_NUM_OVERFLOWS is not read by this adapter
            "package_cache_hit_ratio": round(
                100.0 * (1 - (num(w.get("pkg_cache_inserts"), 0) or 0) / (num(w.get("pkg_cache_lookups"), 0) or 1)), 2
            )
            if num(w.get("pkg_cache_lookups"), 0)
            else None,
            "lock_escalations": int(num(w.get("lock_escals"), 0) or 0),
            "deadlocks": int(num(w.get("deadlocks"), 0) or 0),
            "lock_timeouts": int(num(w.get("lock_timeouts"), 0) or 0),
            "tables_needing_reorg": reorg,
            "tables_needing_runstats": runstats,
            "reorg_pending_tables": pending,
            "log_used_percent": round(100.0 * used / (used + avail), 2)
            if log and avail >= 0 and used + avail > 0
            else None,
            "auto_runstats": cfg.get("auto_runstats"),
            "auto_reorg": cfg.get("auto_reorg"),
            "self_tuning_mem": cfg.get("self_tuning_mem"),
            "mon_act_metrics": cfg.get("mon_act_metrics"),
            "logarchmeth1": cfg.get("logarchmeth1"),
            "locklist": cfg.get("locklist"),
            "maxlocks": cfg.get("maxlocks"),
        }
