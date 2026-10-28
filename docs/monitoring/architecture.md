# Metrics architecture

How KBM DBManager stores and serves monitoring telemetry for hundreds to thousands of managed
database servers. Related documents: [monitoring-pipeline.md](pipeline.md) (write
and read paths), [metrics-catalog.md](metric-catalog.md) (every metric),
[timeseries-storage.md](timeseries-storage.md) (operating the storage),
[retention.md](../administration/retention.md), [scaling.md](../deployment/scaling.md).

## 1. What changed and why

Until this release every metric sample was a row in the PostgreSQL table `metric_samples`
(plus `query_stat_snapshots` for query statistics). That design was simple but does not
scale to an enterprise fleet:

| | PostgreSQL rows (before) | Time-series database (now) |
|---|---|---|
| Storage per sample | **~236 bytes** (measured: 453,376 rows = 107 MB with indexes) | **~1–3 bytes** (measured: 3.3 M samples in 9.8 MB on a fresh store; less after background merges) |
| 30 days, 1,000 servers | ~1.4 TB, ~6 billion rows | ~6–18 GB |
| Downsampling | `date_bin` + `GROUP BY` over raw rows | built into every range query |
| Retention | `DELETE … WHERE ts < …` (bloat, vacuum) | dropping whole partitions natively |
| Effect on the application DB | metrics compete with inventory, RBAC, audit | none — PostgreSQL holds only metadata |

PostgreSQL keeps what is relational and must be transactional: identity, inventory,
configuration, credentials, RBAC, recommendations, security findings, audit, jobs, and the
*metadata* of query telemetry (`query_fingerprints`). It also keeps one small per-server
operational cache (`servers.latest_metrics`, `health_status`) so inventory lists and the
health map render even while the time-series database is down. History never goes to
PostgreSQL any more.

## 2. Capacity analysis

### Workload per monitored server (defaults)

| Source | Series | Cadence | Samples/s |
|---|---|---|---|
| Server metrics (gauges, rates, counters) | ~40–50 | 60 s (NORMAL) | ~0.75 |
| Wait classes | ~5 | 60 s | ~0.08 |
| Storage / capacity gauges | ~5 | 300 s (SLOW, throttled) | ~0.02 |
| Query telemetry: top 50 statements × 10 metrics | ~500 | 300 s (SLOW) | ~1.7 |
| **Total** | **~550 active series** | | **~2.5** |

### Fleet totals

Disk figures use 1 byte/sample (VictoriaMetrics' typical compressed size for DB telemetry
after merges) and 3 bytes/sample (what this project measured on a small fresh store — a
conservative upper bound). PostgreSQL uses the measured 236 bytes/row.

| Servers | Samples/s | Samples/day | Active series | TSDB 30 d | TSDB 90 d | PostgreSQL rows, 30 d |
|---|---|---|---|---|---|---|
| 100 | 250 | 22 M | 55 k | 0.7–2 GB | 2–6 GB | ~150 GB |
| 500 | 1,250 | 108 M | 275 k | 3–10 GB | 10–30 GB | ~760 GB |
| 1,000 | 2,500 | 216 M | 550 k | 6–20 GB | 20–60 GB | ~1.5 TB |

**Measured headroom** (single VictoriaMetrics node in this project's test environment, see
[scaling.md](../deployment/scaling.md)): 110,000 samples/s sustained ingestion through the application's
batch writer — 44× the 1,000-server requirement; fleet-wide aggregates over 1,000 server ids
in 8–10 ms; latest values of 6 metrics for 1,000 servers in one request in 125 ms.

## 3. Technology selection

Requirements: open source, runs as one container with a persistent volume, handles
~1 M active series and a few thousand samples/s on modest hardware, native retention,
query-time aggregation over long ranges, deletion by label (simulated data, removed
servers), deduplication (idempotent retries and migration), and a path to clustering.

| Criterion | **VictoriaMetrics** (selected) | TimescaleDB | InfluxDB |
|---|---|---|---|
| License | Apache-2.0 (single node and cluster) | Apache-2.0 core; compression, continuous aggregates and retention jobs under the Timescale License (TSL) | v2: MIT; v3 Core: MIT/Apache, but Core limits queries to recent data (~72 h) — longer history needs Enterprise |
| Storage efficiency | ~0.4–1 B/sample typical | row-based ingest, 5–20× after columnar compression of old chunks | good (TSM / Parquet) |
| Footprint | one static binary, ~100 MB RAM idle | a second PostgreSQL to tune, vacuum, back up | moderate |
| Query language | MetricsQL (PromQL superset): `rate`, `increase`, `quantile_over_time`, `topk` … | SQL (`time_bucket`) | SQL / InfluxQL (v3), Flux (v2, in maintenance) |
| Retention | `-retentionPeriod`, drops whole partitions | `add_retention_policy` (TSL) | per database/bucket |
| Downsampling | at query time (OSS); stored downsampling is Enterprise | continuous aggregates (TSL) | v2 tasks / v3 external processing |
| Delete by label | `delete_series` with any selector | `DELETE` (expensive on compressed chunks) | v3: limited |
| Dedup of identical samples | `-dedup.minScrapeInterval` | unique index + upsert (slow on hypertables) | last-write-wins per point |
| Scale-out | VictoriaMetrics cluster (vminsert / vmselect / vmstorage), same API | multi-node discontinued | v3 Enterprise / Cloud |
| Kubernetes / OpenShift | official operator and Helm charts, runs as non-root | operators for PostgreSQL | Helm charts |

**Decision: VictoriaMetrics single node (v1.122.0).** It is the smallest, cheapest to run
and most storage-efficient option, its query model fits gauges/counters/rates exactly
(`rate` handles counter resets), deletion by label makes simulated-data removal and server
removal reliable, and it scales out to the cluster version without changing the API. The
trade-off — no stored downsampling in the open-source edition — is covered by query-time
downsampling plus a long single retention (see [retention.md](../administration/retention.md)); at 1–3 bytes
per sample, a year of raw 1-minute data for 1,000 servers is ~70–220 GB, so storing raw data
is affordable.

TimescaleDB was the closest alternative (familiar SQL, same engine as the application DB)
but needs TSL-licensed features for what matters here and would put a second heavily
written PostgreSQL next to the first. InfluxDB 3 Core's query window does not cover the
required 30–90 day history.

## 4. Data model

A sample is `name{labels} value timestamp(ms, UTC)`.

### Naming convention

* prefix `db_` for telemetry of managed databases (`dbm_` is reserved for KBM DBManager's own
  process metrics on `/metrics`);
* snake_case, base unit suffix: `_bytes`, `_seconds`, `_ms`, `_percent`;
* `_total` only for counters, `_per_second` for collector-derived rates;
* engine-specific metrics carry the engine: `db_pg_…`, `db_oracle_…`, `db_mssql_…`.

The API and UI keep using platform keys (`connections.total`); `app/timeseries/catalog.py`
is the single mapping between keys and names.

### Labels and cardinality rules

| Label | Required | Values | Bounded by |
|---|---|---|---|
| `server_id` | yes | server UUID | number of servers |
| `engine` | yes | postgresql, mysql, … | ~10 |
| `data_source` | yes | real, simulated | 2 |
| `database` | no | database name | databases per server |
| `wait_type` | no | engine wait class | engine (≤ ~20) |
| `fingerprint` | no | 16 hex digits | top-N per collection and a per-server cap |
| `tablespace` | no | tablespace / filegroup | per server |

Enforced in code (`app/timeseries/model.py`): any other label key is rejected, values are
limited to 128 characters and a single line, and a sample without the three required
labels cannot be built. Explicitly **never** labels: SQL text, session ids, user input,
exception messages, host names, environment/site/application/owner (inventory attributes
change; as labels they would fork a server's history and multiply series — they are joined
from PostgreSQL at query time instead). Rejected samples are counted in
`dbm_metrics_dropped_total{reason="label_policy"}`.

Query telemetry uses a **fingerprint**: SHA-256 over the engine name and the masked,
literal-free, whitespace-collapsed statement, truncated to 16 hex digits. Two executions
that differ only in literals share a fingerprint; the statement text is stored once in
PostgreSQL (`query_fingerprints`, literals masked) and never in the time-series database.
Cardinality is bounded twice: only the top `DBM_QUERY_STATS_TOP_N` (50) statements by total
time are written per collection, and a server never exceeds
`DBM_QUERY_FINGERPRINTS_MAX_PER_SERVER` (2,000) distinct fingerprints
(`dbm_metrics_dropped_total{reason="query_cardinality"}`).

### Metric types and their semantics

| Type | Stored as | Charted as |
|---|---|---|
| gauge | value | requested aggregation per step (`avg` default; `min`, `max`, `p50`, `p95`, `p99`, `sum`, `count`, `last`) |
| counter | cumulative value (`_total`) | `rate` per second per step (or `increase`); counter resets handled |
| rate | per-second value computed by the collector | aggregation per step |
| distribution | reported statistic (mean / max latency) | aggregation per step |

## 5. Identity and scope

Series are identified by stable ids, never by display names: `server_id` is the inventory
UUID. Renaming a server, moving it to another environment or changing its owner does not
touch its history. Every read starts in PostgreSQL: the API resolves which servers the
caller may see (RBAC scope + real/simulated filter) and the time-series query is restricted
to exactly those `server_id`s. Simulated telemetry carries `data_source="simulated"` and is
removed by that label and by the removed servers' ids — never by name.

## 6. Time handling

All timestamps are UTC end to end: samples are built from timezone-aware datetimes (naive
values are rejected), stored as Unix milliseconds, returned as ISO-8601 with `+00:00`, and
converted to the viewer's local time only in the browser. Custom API ranges without an
offset are interpreted as UTC.
