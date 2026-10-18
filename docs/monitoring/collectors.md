# Collectors and intervals

The backend runs a scheduler and job workers (in one process with `DBM_PROCESS_ROLE=all`, or as
separate `api` and `worker` processes). One worker holds the scheduler lock (PostgreSQL advisory
lock); all workers claim jobs with `FOR UPDATE SKIP LOCKED`, so adding workers scales collection.

| Job | Interval | What it does |
|---|---|---|
| `collect_metrics` | per server (default 60 s; FAST metrics 30 s, SLOW parts every 5 min) | metric snapshot, rate derivation, health |
| `collect_query_stats` | 5 min | top statements (`DBM_QUERY_STATS_TOP_N`, 50) → fingerprints + counters |
| `discover_inventory` | 1 h | version, capabilities, database list |
| `generate_recommendations` | 15 min | performance rules over the last 6 h of history |
| `security_scan` | 6 h | security rules, privileged-account snapshot |
| `retention` | 6 h | prune legacy tables, stale fingerprints, job history, expired sessions |
| `timeseries_deletes` | 1 min | retry time-series deletions for removed servers |
| `simulation` | on request | generate/remove a simulated fleet |

Interval classes are configurable: `DBM_INTERVAL_FAST_SECONDS` (30), `DBM_INTERVAL_NORMAL_SECONDS`
(60), `DBM_INTERVAL_SLOW_SECONDS` (300), `DBM_INTERVAL_VERY_SLOW_SECONDS` (3600). Each per-server job
starts at a stable offset of up to `DBM_COLLECTION_JITTER_RATIO` (20 %) of its interval so a large
fleet is spread evenly. Concurrency: `DBM_COLLECTOR_CONCURRENCY` (16) target connections per worker.

## Circuit breaker

After `DBM_CIRCUIT_FAILURE_THRESHOLD` (3) consecutive connection failures a server's collection is
paused with a back-off growing from 1 minute to 1 hour. The server header shows *Collection paused after repeated
failures until …*; updating its credentials or **Collect now** resets the breaker.

## Collector health

**Platform → Collectors** shows the scheduler leader, pending jobs, failures per job kind and
paused servers. Prometheus metrics: `dbm_collector_jobs_total`, `dbm_collector_job_duration_seconds`,
`dbm_collector_inflight`, `dbm_target_connections_open`, `dbm_scheduler_leader`.

Per server, **Overview → Collection status** shows each component's last result. A missing
privilege marks only that component *Insufficient privilege*; the server stays connected.
