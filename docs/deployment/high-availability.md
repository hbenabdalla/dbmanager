# High availability

KBM DBManager scales horizontally: any number of API replicas and workers share one control-plane
PostgreSQL database and one time-series store. This page describes how the replicas coordinate,
how to see them, and how to encrypt their connections to the databases. Sizing is in
[scaling](scaling.md); the Kubernetes installation in [Kubernetes and OpenShift with Helm](kubernetes-helm.md).

## What each process does

| Role (`DBM_PROCESS_ROLE`) | Serves | Runs |
|---|---|---|
| `api` | the REST API and the UI's requests | nothing in the background |
| `worker` (`python -m app.worker`) | probes only (`/healthz`, `/readyz`, `/metrics` on `DBM_WORKER_PORT`) | collectors, alert evaluation, schedules, reports, backups |
| `all` (default, Docker Compose) | both | both |

API replicas are stateless (sessions, rate-limit windows and jobs are in PostgreSQL), so any
replica can answer any request and a load balancer needs no session affinity.

## Scheduler leader election

Workers claim collection jobs with `FOR UPDATE SKIP LOCKED`, so each job runs once and adding
workers adds capacity. Exactly one worker at a time is the **scheduler leader** that enqueues the
due jobs. Leadership is a PostgreSQL session advisory lock on a dedicated connection; when the
leader stops or its pod is deleted its session ends and another worker takes the lock at its next
tick (`DBM_SCHEDULER_TICK_SECONDS`, 15 s). Details are in
[scaling](scaling.md#several-workers-scheduler-leadership). CI deletes the leader pod of a kind
deployment and checks that another worker leads (`scripts/k8s-failover-check.py`); the backend
suite kills a leader process with SIGKILL and checks the same (`tests/api/test_leader_failover.py`).

## Concurrent start and migrations

When several replicas start at once (a new deployment, a rolling upgrade, a node drain) each runs
its start-up work — database migrations, the first administrator, data upgrades — under a
PostgreSQL advisory lock: one replica does the work and the others wait, then find it done.
Alembic migrations take their own advisory lock as well, so `python -m app.cli migrate` run by a
Job at the same time as starting replicas is safe too. Before 0.15, replicas starting together on
an empty database could fail with duplicate-object errors; `tests/migrations/test_concurrent_start.py`
starts three at once.

## The instance registry

Every API and worker process records a heartbeat in the `platform_instances` table every
`DBM_INSTANCE_HEARTBEAT_SECONDS` (15 s, 5–300) with its role, host name (the pod name on
Kubernetes), version, commit and whether it is the scheduler leader. A process that stops cleanly
removes its row; one that disappears (killed, node lost) is shown as **stale** after three missed
heartbeats and removed after a day. The table is not included in platform backups.

Where to see it:

* **Platform → Collectors → Deployment** lists every process, its version, its last heartbeat and
  the scheduler leader, and warns about mixed versions and about a deployment without any worker;
* **Platform → About** shows a one-line summary and whether background jobs run in this process,
  in another replica (`running elsewhere`) or nowhere;
* the command palette entry *Deployment and scheduler leader* opens the Collectors page;
* `GET /api/v1/platform/instances` (permission `collectors.view`) returns the same data.

## TLS to the control-plane PostgreSQL

| Variable | Description |
|---|---|
| `DBM_DATABASE_TLS_MODE` | empty (default: as `DBM_DATABASE_URL` says), `disable`, `prefer`, `require` (encrypted, server not verified), `verify-ca` (certificate signed by the CA), `verify-full` (CA and host name) |
| `DBM_DATABASE_TLS_CA_FILE` | CA certificate (PEM) for `verify-ca` and `verify-full`; the system trust store when empty |

The setting applies to the application, the migrations and the start-up work alike; TLS 1.2 is the
minimum. Use `verify-full` in production. With the Helm chart: `postgresql.tls.mode` and
`postgresql.tls.caSecret`. The backend CI runs the API suite against a PostgreSQL that serves TLS
with `verify-full` (`tests/api/test_control_plane_tls.py`).

## TLS to the time-series store

Point `DBM_TIMESERIES_URL` at an `https://` endpoint (VictoriaMetrics with `-tls`, or a gateway in
front of a cluster). `DBM_TIMESERIES_TLS_CA_FILE` names the CA certificate (PEM) when it is not in
the system trust store; certificate and host name are always verified. The setting is used by the
collectors, the dashboards, the platform backup export and `scripts/backup.sh`. With the Helm
chart: `timeseries.external.url` and `timeseries.tls.caSecret`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Collectors page: *No worker is running* | every process has `DBM_PROCESS_ROLE=api`: start a worker (`worker` or `all`) |
| No scheduler leader, but workers are active | the leader just stopped (wait one tick), or the workers cannot reach PostgreSQL: check their logs and `/readyz` |
| Instances shown as stale | the pod was killed or its node lost; it disappears after a day. Many stale rows after each rollout mean the pods are not stopped gracefully (raise `terminationGracePeriodSeconds`) |
| *Mixed versions* badge | a rolling upgrade is in progress, or an old replica (another Deployment, a forgotten container) is still connected to the same database |
| `certificate verify failed` at start | `DBM_DATABASE_TLS_CA_FILE` / `DBM_TIMESERIES_TLS_CA_FILE` must be the CA that signed the server certificate; for `verify-full` the host in the URL must be in its subject alternative names |
| Start-up seems to hang on one replica | it waits for the start-up lock held by another replica that is migrating; check that replica's log |
