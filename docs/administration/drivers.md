# Database drivers

**Platform → Configuration → Database drivers** (`GET /api/v1/platform/drivers`) shows, for the
running backend, each driver's version and status:

| Driver | Engines | Status values |
|---|---|---|
| asyncpg | PostgreSQL | Installed / MISSING |
| PyMySQL | MySQL, MariaDB | Installed / MISSING |
| pyodbc + Microsoft ODBC Driver 18 | SQL Server | Installed / Degraded (only ODBC Driver 17) / **MISSING** |
| python-oracledb (thin) | Oracle | Installed / MISSING |
| ibm_db (bundled clidriver) | Db2 LUW, Db2 for z/OS | Installed / MISSING |

Status reflects runtime reality: when `pyodbc` is installed but no Microsoft ODBC driver is
registered with unixODBC the page shows **SQL Server Driver MISSING** with the remediation, the
onboarding wizard shows the same banner, and connection tests fail fast with `DRIVER_NOT_INSTALLED`
and *"Microsoft ODBC Driver 18 is required."*

## SQL Server driver in the image

The backend image installs `msodbcsql18` from `packages.microsoft.com` (accepting the Microsoft
EULA) and **fails the build** when it cannot. Build arguments:

| Argument | Default | Effect |
|---|---|---|
| `INSTALL_MSODBC` | `true` | `false` skips the driver entirely |
| `MSODBC_OPTIONAL` | `false` (`DBM_MSODBC_OPTIONAL` in `.env`) | `true` turns an installation failure into a warning; SQL Server then reports MISSING |

See [SQL Server ODBC driver missing](../troubleshooting/engines.md#sql-server-odbc-driver-missing).
