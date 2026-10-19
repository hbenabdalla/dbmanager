# IBM Db2 for z/OS

## Supported versions

Db2 12 and 13 for z/OS. **Support level: PARTIAL.** Not real-engine validated — Db2 for z/OS cannot run in a container; validation against a real subsystem through Db2 Connect is required (external).

## Driver

`ibm_db` over DRDA. **Db2 Connect licensing is required**: a Db2 Connect license file on the client, or server-side activation on the subsystem. Without it, connections fail with `SQL1598N` / `SQL8002N`.

## Connection parameters

Host, DDF port, location name (database), username (RACF/ACF2/Top Secret ID), password, and the *Db2 Connect* option.

## TLS

TLS through the subsystem's secure DDF port (AT-TLS).

## Privileges

Authorization IDs are RACF/ACF2/Top Secret identities; DRDA access from DBManager also needs
**Db2 Connect** (a license file on the client, or server-side activation of the subsystem).

### Minimum inventory / monitoring account

The z/OS adapter reads the catalog and real-time statistics only:

```sql
GRANT SELECT ON SYSIBM.SYSDATABASE        TO DBMMON;
GRANT SELECT ON SYSIBM.SYSTABLES          TO DBMMON;
GRANT SELECT ON SYSIBM.SYSINDEXES         TO DBMMON;
GRANT SELECT ON SYSIBM.SYSCOLUMNS         TO DBMMON;
GRANT SELECT ON SYSIBM.SYSROUTINES        TO DBMMON;
GRANT SELECT ON SYSIBM.SYSTRIGGERS        TO DBMMON;
GRANT SELECT ON SYSIBM.SYSSEQUENCES       TO DBMMON;
GRANT SELECT ON SYSIBM.SYSTABLESPACESTATS TO DBMMON;   -- real-time statistics
GRANT SELECT ON SYSIBM.SYSUSERAUTH        TO DBMMON;   -- authority analysis
GRANT SELECT ON SYSIBM.SYSDBAUTH          TO DBMMON;
GRANT SELECT ON SYSIBM.SYSTABAUTH         TO DBMMON;
GRANT SELECT ON SYSIBM.SYSROLES           TO DBMMON;
GRANT SELECT ON SYSIBM.SYSCONTEXT         TO DBMMON;
```

Sites that already grant `SQLADM` (or `SYSCTRL` with `SEPARATE_SECURITY = NO`) to monitoring IDs
cover these, but the explicit list above is the least-privilege form. RACF must permit the ID to
connect through DDF (`DSNR` class, subsystem `ssid.DIST`).

EXPLAIN (optional): create `PLAN_TABLE` and `DSN_STATEMNT_TABLE` under the monitoring SQLID (sample
job DSNTESC) and grant `EXPLAIN` system privilege:

```sql
GRANT EXPLAIN TO DBMMON;
```

### Not covered by SQL

Subsystem performance (statistics and accounting traces, IFCID records, SMF, CPU/zIIP usage) is
not reachable by a SQL client. DBManager marks those capabilities **REQUIRES EXTERNAL
MONITORING**; use a z/OS monitor such as OMEGAMON for Db2.

### DBA management account

Administration actions are not offered for Db2 for z/OS; changes go through the site's change
management. No administration credential is needed.

See [Least-privilege accounts](permissions.md) for the rules that apply to every engine.

## Supported capabilities

The z/OS adapter is separate from Db2 LUW and reads the SYSIBM catalog and real-time statistics only.

| Area | Status |
|---|---|
| Connection (DRDA) | SUPPORTED (Db2 Connect licence) |
| Version, location, SSID, data sharing group, APPLCOMPAT | SUPPORTED |
| Databases, schemas, tables, views, indexes, routines, triggers, sequences | SUPPORTED |
| Tablespaces and sizes (real-time statistics) | SUPPORTED |
| Authorities and grants (SYSUSERAUTH, SYSDBAUTH, SYSTABAUTH) | SUPPORTED |
| Identities / groups (RACF, ACF2, Top Secret) | EXTERNAL MONITORING REQUIRED |
| EXPLAIN (PLAN_TABLE) | PARTIAL — access path only |
| REORG / RUNSTATS / COPY recommendations from RTS | PARTIAL — recommendations only |
| Subsystem performance, CPU/zIIP, buffer pools, waits (IFI/IFCID, SMF, traces) | EXTERNAL MONITORING REQUIRED |
| Threads and locks | NOT IMPLEMENTED |
| Dynamic statement cache, DSNZPARMs | NOT IMPLEMENTED |
| Administration actions | NOT IMPLEMENTED (changes go through change management) |

The authoritative per-capability status is in the [support matrix](index.md#implemented-capabilities) and,
for a registered server, on its **Overview → Capabilities** (runtime discovery refines every cell).

## Known limitations

* No thread, lock or subsystem performance monitoring: use a z/OS monitor (for example OMEGAMON for Db2). DBManager does not fabricate these values.
* No administration actions.
* Not validated against a real subsystem.

## Engine-specific metrics

Besides the [common metric catalog](../monitoring/metric-catalog.md), this adapter reports:
None beyond the common catalog.

## Security checks

| Rule | Checks |
|---|---|
| `ZOS-SEC-001` | Many SYSADM / SYSCTRL holders |
| `ZOS-SEC-002` | PUBLIC table privileges |

The common rules `GEN-SEC-001` (platform connects without TLS) and `GEN-SEC-002` (dormant
privileged accounts) also apply.

## Recommendations

| Rule | Checks |
|---|---|
| `ZOS-PERF-001` | REORG indicated by real-time statistics |
| `ZOS-PERF-002` | RUNSTATS indicated by real-time statistics |
| `ZOS-PERF-003` | Image COPY overdue |

Plus the common performance rules `GEN-PERF-001` … `GEN-PERF-010`. Recommendations are advice with
evidence; DBManager never executes them.

## Troubleshooting

* `SQL1598N` / `SQL8002N`: Db2 Connect licence missing.
* Connection refused by RACF: the ID needs DDF access (`DSNR` class, `ssid.DIST`).
