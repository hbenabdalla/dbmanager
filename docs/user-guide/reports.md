# Reports

**Overview → Reports** lists the available reports; each downloads as **CSV** and contains only
servers you may see.

| Report | Contents | Permission |
|---|---|---|
| Fleet health | health status and reasons per server | `server.view` |
| Database inventory | servers with ownership and placement | `server.view` |
| Capacity | database size and 7-day growth | `performance.view` |
| Performance snapshot | latest key metrics per server | `performance.view` |
| Slow queries | statements averaging over 100 ms (masked text) | `query.view` |
| Security posture | open security findings | `security.view` |
| Privileged users | administrative database accounts | `user.view` |
| Recommendations | open performance recommendations | `performance.view` |
| Audit activity | daily audit event counts (30 days) | `audit.view` |

PDF and Excel formats and scheduled delivery are not available yet (shown as *planned*; see the
[roadmap](../product/roadmap.md)).
