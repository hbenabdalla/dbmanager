# Scaling

## Measured performance

Test environment: one VictoriaMetrics v1.122.0 single node, backend (uvicorn, one process,
API + worker) and PostgreSQL 16 on the same small cloud VM; API timings are the median of 5
requests through the full stack (authentication, RBAC, PostgreSQL scope query, time-series
query, JSON).

**Ingestion** — 1,000 servers × 40 metrics × 60 minutes = 2.4 M samples through the
application's batch writer: **110,000 samples/s**. A 1,000-server fleet needs ~2,500/s.

**Simulation generation** (PostgreSQL metadata + bulk telemetry import + advice):

| Profile | Servers | Samples written | Time |
|---|---|---|---|
| Small | 5 | 70 k | 3.4 s |
| Standard | 18 | 598 k | 12.4 s |
| Large | 75 | 2.8 M | 52 s |

**API latency** (Standard → Large profile):

| Request | Standard (19 servers) | Large (76 servers) |
|---|---|---|
| Fleet dashboard (3 fleet trend queries + PostgreSQL KPIs) | 36 ms | 52 ms |
| Server performance tab, 16 metrics, 6 h | 23 ms | 21 ms |
| … 7 days, p95 | 24 ms | 23 ms |
| … 90 days | 13 ms | 17 ms |
| Fleet sum, 24 h | 15 ms | 25 ms |
| Compare 6 servers, 24 h (one request) | 22 ms | 23 ms |
| Current values, all servers (one request) | 28 ms | 54 ms |
| Top queries, fleet, 24 h | 62 ms | 82 ms |
| Baseline, 7 days | 30 ms | 42 ms |
| Capacity trend, fleet | 21 ms | 40 ms |

Every chart series stayed at or below 240 points (90 days: 60 points of 12 h after the
history start).

**1,000 servers** (synthetic, 40 metrics each): fleet aggregate over 1,000 ids in 8–10 ms;
latest values of 6 metrics for 1,000 servers in 125 ms.

## Limits of one node and what to change

| Fleet | Time-series | Backend | PostgreSQL |
|---|---|---|---|
| ≤ 300 servers | default container (2 CPU, 2 GB) | compose, role `all` | default |
| 300–1,500 | 4 CPU, 4–8 GB, SSD volume sized per [metrics-architecture.md §2](../monitoring/architecture.md) | split `api` and `worker` roles; 2+ workers, `DBM_COLLECTOR_CONCURRENCY` 32–64 | 2–4 CPU |
| 1,500–5,000 | VictoriaMetrics cluster (vminsert ×2, vmselect ×2, vmstorage ×3) or managed | 4+ workers, consider raising `collection_interval_s` for non-production | HA PostgreSQL |
| > 5,000 | cluster + `vmagent` in front for buffering/replication; evaluate a cache for current fleet health | sharded worker pools | |

Signals to watch (backend `/metrics` on port 8000 — unauthenticated, not proxied by nginx — and
VictoriaMetrics `/metrics`):
`dbm_metrics_buffer_pending` growing, `dbm_metrics_write_failures_total` increasing,
`dbm_timeseries_query_latency_seconds` p95 > 1 s, `vm_slow_inserts_total`,
`vm_cache_entries{type="storage/hour_metric_ids"}` close to the series count (memory),
active series (`/api/v1/status/tsdb`).

## Several workers: scheduler leadership

Any number of processes can run the workers (`DBM_PROCESS_ROLE=all` or `worker`); they claim jobs
from `collector_jobs` with `FOR UPDATE SKIP LOCKED`, so adding workers scales collection. Exactly
one process at a time is the **scheduler leader** and enqueues due jobs (`app/jobs/runner.py`):

* leadership is a session-level PostgreSQL advisory lock held on a **dedicated connection** taken
  from the pool and kept for as long as the process leads;
* every `DBM_SCHEDULER_TICK_SECONDS` (15 s) the leader re-checks the lock on that connection; if the
  session was lost the check fails and the process drops leadership;
* on any error the connection is **invalidated, never returned to the pool** — a pooled session
  would keep the advisory lock and stop every other instance from taking over — and a fresh
  connection is opened at the next tick;
* on shutdown the connection is closed, which releases the lock at once for the next instance.

Failover therefore takes about one tick after the leader's PostgreSQL session ends. The gauge
`dbm_scheduler_leader` (1 on the leader) on each process's `/metrics` shows which one leads;
**Platform → Collectors** shows it in the UI. `/metrics` is served on the backend port 8000 (and on
`DBM_WORKER_PORT` of a stand-alone worker) and is not proxied by the frontend
([Diagnostics](../administration/diagnostics.md)).

## Cardinality

Series per server ≈ 50 server metrics + wait classes + 10 × statements in the top-N. The
query telemetry dominates: lower `DBM_QUERY_STATS_TOP_N` before adding hardware. Churn is
limited by the per-server fingerprint cap; a fingerprint that leaves the top-N simply stops
receiving samples and ages out with retention.

## Cache decision

No Redis. Measured hot paths are already fast without one (fleet dashboard 52 ms, current
values for 1,000 servers 125 ms, metric catalog served from code), and the frontend
deduplicates and caches requests. Re-evaluate a small shared cache (in-process TTL or Redis)
for current fleet health above ~5,000 servers or with many concurrent dashboard viewers.

## Kubernetes / OpenShift

`deployment/kubernetes/15-timeseries.yaml`: VictoriaMetrics `StatefulSet` with a
PersistentVolumeClaim, ClusterIP service `timeseries`, `NetworkPolicy` that admits only the
API and worker pods, credentials from the `dbmanager-secrets` Secret, non-root and
read-only root filesystem (restricted SCC compatible). The application code has no Docker
assumptions: the URL, credentials and retention are environment variables, and the
repository interface allows replacing the single node with a VictoriaMetrics cluster
(`DBM_TIMESERIES_URL` pointing at vmselect/vminsert through a small gateway) or a managed
Prometheus-compatible service.
