# Monitoring profile

The setup wizard sets the default collection interval for servers registered **afterwards**
(`POST /api/v1/platform/setup/monitoring`, stored as `collection.default_interval_s`):

| Profile | Interval | Use |
|---|---|---|
| Light | 300 s | large estates, minimal overhead |
| Standard | 60 s | recommended default |
| Intensive | 15 s | short investigations, small fleets |

Existing servers keep their interval; change it per server under **Settings**. Some data is always
collected on its own schedule (query statistics every 5 minutes, inventory every hour, security
scan every 6 hours) — see [Collectors and intervals](../../monitoring/collectors.md).
