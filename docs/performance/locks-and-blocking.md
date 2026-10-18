# Locks and blocking

Sessions and locks are read live from the engine when you open the page; blocked-session and lock
counts are also collected as metrics (`sessions.blocked`, `locks.waiting`, `deadlocks`).

| Engine | Blocking information |
|---|---|
| PostgreSQL | `pg_locks` + `pg_blocking_pids()` |
| MySQL | `performance_schema.data_lock_waits` + `INNODB_TRX` |
| MariaDB | `information_schema.INNODB_LOCK_WAITS` (PARTIAL) |
| SQL Server | `sys.dm_tran_locks`, `sys.dm_exec_requests.blocking_session_id` |
| Oracle | `V$LOCK`, `V$SESSION.BLOCKING_SESSION` (sessions identified as `sid,serial#`) |
| Db2 LUW | `MON_GET_LOCKS`, `MON_GET_APPL_LOCKWAIT` (sessions are application handles) |
| Db2 for z/OS | not implemented |

Each blocked session lists the session(s) blocking it, so chains can be followed to the root
blocker. Rules: `GEN-PERF-003` (sessions blocked by locks), `GEN-PERF-004` (deadlocks),
`GEN-PERF-006` (long-running transactions), `PG-PERF-007` (idle in transaction), `DB2-PERF-005`/`009`
(escalations, deadlocks and lock timeouts).

Cancelling or terminating a session is an administration action — see
[Sessions and locks](../user-guide/sessions-and-locks.md#cancel-and-terminate).
