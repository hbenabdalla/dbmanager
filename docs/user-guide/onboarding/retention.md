# Retention

The retention step (and **Platform → Configuration → Monitoring storage**) sets how many days of metric history are
kept (1–3,650 days; `PUT /api/v1/platform/monitoring-storage/retention`, audited).

The policy is stored in the control plane; the time-series database enforces its own start-up
setting `DBM_TIMESERIES_RETENTION`. When the two differ, Monitoring storage says so and shows the
command to apply it:

```bash
# .env
DBM_TIMESERIES_RETENTION=180d
docker compose up -d timeseries
```

Full details: [Retention and downsampling](../../administration/retention.md).
