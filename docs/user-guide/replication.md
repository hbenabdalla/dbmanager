# Replication

A server's **Replication** tab shows its role (standalone, primary, replica), replicas or upstream,
replication state and lag, plus the [replication diagram](topology.md#replication-topology) for
its cluster. **Operations → Monitoring → Replication** lists all replicated servers with lag.

| Engine | Source |
|---|---|
| PostgreSQL | `pg_stat_replication`, `pg_stat_wal_receiver`, replication slots |
| MySQL / MariaDB | `SHOW REPLICA STATUS`; Galera status on MariaDB clusters |
| SQL Server | Always On availability group DMVs (PARTIAL) |
| Oracle | Data Guard destinations and `V$DATAGUARD_STATS` (PARTIAL) |
| Db2 LUW | `MON_GET_HADR` (PARTIAL) |
| Db2 for z/OS | not available over SQL (external monitoring) |

`replication.lag_seconds` above 60 s makes a server *warning*, above 300 s *critical*.
