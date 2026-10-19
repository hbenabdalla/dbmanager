# Security policy

DBManager connects to production databases with stored credentials, so security reports are
taken seriously and handled privately.

## Supported versions

DBManager has not had a stable (1.0) release yet. Until then, only the latest pre-release on the
`main` branch receives security fixes.

| Version | Supported |
|---|---|
| `main` / latest `0.x` pre-release | Yes |
| Older `0.x` pre-releases | No — upgrade to the latest |

## Reporting a vulnerability

**Do not open a public GitHub issue, discussion or pull request for a security problem.**

Report it privately through GitHub's private vulnerability reporting:

1. Open the repository's **Security** tab: <https://github.com/hbenabdalla/dbmanager/security>.
2. Choose **Report a vulnerability** and fill in the advisory form.

Only the maintainers can see the report. If private reporting is not available on the repository
yet, contact the repository owner through their GitHub profile and ask for a private channel
before sharing any details.

Please include:

* the affected version or commit and the deployment type (Docker Compose, Kubernetes, …);
* the component (backend API, frontend, a database adapter, the collector, …);
* steps to reproduce or a proof of concept, and the impact you observed;
* whether the issue requires authentication, and which role.

Do not access data that is not yours, do not run destructive tests against systems you do not
own, and give the maintainers reasonable time to release a fix before any disclosure.

## What to expect

* Acknowledgement of the report, and an initial assessment once it has been reproduced.
* A fix on `main`, a patched pre-release and a GitHub security advisory crediting you (unless you
  prefer otherwise).
* If credentials could have been exposed, the advisory states which ones must be rotated.

## Scope notes for deployers

* The stored database credentials are encrypted with `DBM_MASTER_KEY`. Keep `.env` out of
  version control (it is in `.gitignore`), back the key up separately, and rotate it as described
  in the operations documentation.
* Monitoring accounts should hold only the privileges documented for each engine under
  `docs/database-engines/`; administration is disabled per server until explicitly enabled.
* Expose the application through TLS (reverse proxy or ingress) and keep the PostgreSQL and
  time-series containers on the internal network.
