# Dashboard

![Dashboard](../assets/screenshots/dashboard-overview-dark.png)

The fleet overview (**Overview → Dashboard**) answers "is anything wrong, and where?".

* **KPI row** — Servers, Healthy, Warning, Critical, Unavailable, Databases (with total size),
  Sessions (with a 24-hour sparkline and change), Critical findings (critical + high), Open advice
  (recommendations) and, with `alerts.view`, Active alerts (opens the alert list).
* **Filters** — selecting *Healthy*, *Warning*, *Critical* or *Unavailable*, an engine bar or an
  environment row filters the fleet map and the attention list in place. The filter is kept in the
  URL; **Open in server list** carries it to Overview → Fleet → All servers; **Clear** resets it.
* **Fleet health** — one tile per server grouped by environment; tile size shrinks with fleet size
  so thousands of servers still fit. Select a tile to open the server.
* **Trends** — connections, mean statement latency and transactions per second over 24 hours.
* **Servers requiring attention**, **Top recommendations**, **Security findings** (open, by
  severity; **Review** opens the findings) and **Capacity** (largest servers by database size).
* **Setup checklist** for users with `settings.manage` at platform scope.

Health is computed from the latest metrics: see [Health calculation](../monitoring/health.md).
Auto-refresh can be set from the toolbar; the dashboard never polls faster than once a minute.
