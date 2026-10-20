# Investigate a slow query

**Goal:** find the statement that makes a server slow, see whether it got slower recently, read
its execution plan, and decide on a fix.

## Before you start

* `query.view` on the server (statement statistics with masked literals; `query.view_text` shows
  the full text). `query.explain` for estimated plans; `query.explain_analyze` to execute the plan.
  The Performance Engineer and DBA Administrator roles hold all of them.
* The engine's statement statistics must be available on the server: `pg_stat_statements` on
  PostgreSQL, `performance_schema` digests on MySQL/MariaDB, Query Store or DMVs on SQL Server,
  `V$SQLSTATS` on Oracle, the package cache on Db2. If not, the server's **Queries** tab is disabled
  and its tooltip says what is missing.

## Steps

1. Open **Operations → Queries → Query performance** for the whole fleet, or the server page's
   **Queries** tab for one server.

    ![Query performance](../../assets/screenshots/query-performance.png)

2. Set **Sort by** (for example *Sort by total time* to find what costs the most overall, *Sort by
   mean latency* for the slowest single executions) and the **Activity window** (last hour … 7 days).
   The top panel shows the share of the top statements in the chosen metric.
   **Operations → Queries → Slow queries** lists only statements averaging more than 500 ms.
3. Select the statement. The detail shows the parameterized text, its counters and the **Last 24
   hours** of mean latency and executions per second. A step in latency points at a change; a rise
   in executions points at the application.
4. Check whether it regressed: **Operations → Queries → Regressions** lists statements whose mean
   latency is well above their own baseline, with plan changes on Oracle and SQL Server.
   **Compare periods** compares two periods (for example *last 24 h vs the same day a week ago*).
5. In the statement detail select **Explain this query**. **Operations → Queries → Execution plans**
   opens with the server, database and statement filled in. Replace parameter placeholders with
   real values where the engine needs them.
6. Select **Explain (estimate)**. The statement is not executed. Read the **Plan** (graph, tree or
   raw) and **Detected issues** (full scans, spills, expensive joins, misestimates).
7. Only if estimates are not enough: **Explain analyze (executes)**. The statement runs inside a
   transaction that is rolled back; type *ANALYZE* (or the server name on production servers and
   for modifying statements) and select **Run and roll back**. Load, locks and side effects such
   as sequence increments still happen.
8. To compare with a later plan, enter a label in **Save plan as** before running EXPLAIN; **Compare
   saved plans** (or **Operations → Queries → Compare plans**) shows two saved plans side by side.
9. Check the server's **Recommendations** tab and **Optimization → Recommendations → Query** for
   advice that already covers the statement (for example a missing index).

## What you should see

* The plan's most expensive operators are shaded, and the misestimate badge marks operators whose
  actual rows differ from the estimate by 10× or more.
* EXPLAIN and EXPLAIN ANALYZE runs are in the audit log (`query.explain`, `query.explain_analyze`).

Fixing the statement (an index, a rewrite, a statistics refresh) is a normal change: on a server
with *Administration enabled*, use the **Administration** tab (*create index*) or the SQL workspace;
both preview, confirm and audit the change. After a recommendation is resolved, **Optimization →
Recommendations → Outcomes** shows whether latency improved.

## If it does not work

* **Explain analyze** is missing: your role lacks `query.explain_analyze`.
* *EXPLAIN ANALYZE of a data-modifying statement requires query.modify*, or `409 monitoring_only`:
  modifying statements need `query.modify` and a server registered as *Administration enabled*.
* *EXPLAIN ANALYZE on production servers is disabled by platform settings*: the setting
  `workspace.allow_explain_analyze_in_production` is off ([Administration](../../administration/index.md#configuration)).
* No statements are listed: statistics are sampled every 5 minutes; check the collection status on
  the server's **Overview** tab and [Monitoring problems](../../troubleshooting/monitoring.md).

Reference: [Queries and plans](../queries-and-plans.md),
[Execution plans](../../performance/execution-plans.md),
[Query statistics](../../performance/query-statistics.md).
