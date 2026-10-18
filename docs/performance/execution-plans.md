# Execution plans

| Engine | Estimate | Actual (explicit, rolled back) |
|---|---|---|
| PostgreSQL | `EXPLAIN (FORMAT JSON)`; generic plans for `$1` parameters on 16+ | `EXPLAIN (ANALYZE, BUFFERS)` |
| MySQL | `EXPLAIN FORMAT=TREE/JSON` | `EXPLAIN ANALYZE` (8.0.18+) |
| MariaDB | `EXPLAIN FORMAT=JSON` | `ANALYZE FORMAT=JSON` |
| SQL Server | `SET SHOWPLAN_XML ON` (compiled, not executed) | `SET STATISTICS XML ON` |
| Oracle | `EXPLAIN PLAN` into `PLAN_TABLE` | `STATISTICS_LEVEL=ALL` + `V$SQL_PLAN_STATISTICS_ALL` |
| Db2 LUW | `EXPLAIN PLAN` into the explain tables | not supported |
| Db2 for z/OS | `EXPLAIN` into `PLAN_TABLE` (access path only) | not supported |

*Actual* plans execute the statement inside a transaction that is always rolled back (read-only
for queries). They require `query.explain_analyze` (plus `query.modify` for DML) and typed
confirmation on production; administrators can disable them on production servers.

## Plan analysis

Every plan is normalized into a common operator tree (operator, relation, index, estimated and
actual rows, cost, time, buffers, loops) while the **raw vendor plan** is kept and shown unchanged.
The analyzer flags, with severity and the operator concerned: `FULL_SCAN`, `KEY_LOOKUP`,
`INEFFICIENT_FILTER`, `SORT_SPILL`, `LARGE_SORT`, `TEMP_SPILL`, `EXPENSIVE_NESTED_LOOP`,
`CARDINALITY_MISESTIMATE`, `EXCESSIVE_READS`, `DOMINANT_OPERATOR`, plus warnings reported by the
engine itself (for example SQL Server missing-index hints, shown as evidence only).

The UI shows a graph (cost heat, rows between operators, misestimate badges at 10×), the tree and
the raw plan — see [Queries and plans](../user-guide/queries-and-plans.md#execution-plans).
