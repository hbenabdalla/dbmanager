# Requirements

## Host

| Fleet size | CPU | Memory | Disk |
|---|---|---|---|
| Evaluation / up to ~50 servers | 2 vCPU | 4 GB | 20 GB SSD |
| Up to ~300 servers | 4 vCPU | 8 GB | 50–100 GB SSD |
| Larger fleets | see [Scaling](../deployment/scaling.md) | | |

* Docker Engine 24+ with Docker Compose v2 (Linux, or Docker Desktop on Windows/macOS).
* Outbound network access from the backend container to the database servers you register.
* For building the images: access to the Python and npm package registries, and to
  `packages.microsoft.com` for the SQL Server ODBC driver (see [Database drivers](../administration/drivers.md)).

## Browser

A current Chromium-based browser, Firefox or Safari.

## Databases you want to manage

Network reachability from the backend and a dedicated monitoring account per server, created with
the grants listed on each [engine page](../database-engines/index.md).
