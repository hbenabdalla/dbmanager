# Waits

`wait.active_sessions` is sampled at every collection: the number of active sessions per wait
class/type. It is shown as *Active sessions by wait type* on the server's Performance tab and used by
rules such as `MSSQL-PERF-002` (page I/O latch waits dominate) and `ORA-PERF-002` (user I/O dominates).

| Engine | Source | Level |
|---|---|---|
| PostgreSQL | `pg_stat_activity.wait_event_type / wait_event` | FULL |
| MySQL / MariaDB | `performance_schema` wait instrumentation | PARTIAL |
| SQL Server | `sys.dm_os_waiting_tasks`, `sys.dm_os_wait_stats` | FULL |
| Oracle | `V$SESSION` wait class / event, `V$SYSTEM_WAIT_CLASS` (never ASH) | FULL |
| Db2 LUW | `MON_GET_CONNECTION` wait times | PARTIAL |
| Db2 for z/OS | external monitoring | — |

Sampling at the collection interval shows the shape of the load, not every short wait. Oracle ASH
would be finer-grained but is part of the Diagnostics Pack and is not used (see
[Oracle](../database-engines/oracle.md#licensed-features-diagnostics-and-tuning-pack)).
