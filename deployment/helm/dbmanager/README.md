# DBManager Helm chart

<img src="https://raw.githubusercontent.com/hbenabdalla/dbmanager/main/docs/assets/brand/dbmanager-logo.png" alt="DBManager" width="260">

[![Artifact Hub](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/dbmanager)](https://artifacthub.io/packages/search?repo=dbmanager)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/hbenabdalla/dbmanager/blob/main/LICENSE)
[![Documentation](https://readthedocs.org/projects/db-manager/badge/?version=latest)](https://db-manager.readthedocs.io/en/latest/)

**DBManager** is a self-hosted platform for administering, monitoring and securing a fleet of
relational databases from one place: PostgreSQL, MySQL, MariaDB, Microsoft SQL Server, Oracle
Database, IBM Db2 LUW and (partially) IBM Db2 for z/OS.

This chart deploys DBManager on **Kubernetes 1.27+** and **OpenShift 4.14+**.

## What DBManager does

- **Fleet monitoring:** health, sessions, locks and blocking, wait classes, storage and
  replication, collected into VictoriaMetrics, with dashboards and a topology view.
- **Query performance:** statement statistics, fingerprints, regressions and graphical execution
  plans.
- **Advisors:** 116 performance and security rules with evidence, plus capacity forecasts and
  checks of whether a recommendation helped.
- **Alerting:** rules, maintenance windows, and notifications by e-mail, Slack, Teams and webhooks.
- **Guarded administration:** servers are monitoring-only by default; every change is previewed,
  confirmed and audited.
- **Backup & restore** of the platform and of the managed databases.
- **Enterprise access:** LDAP/AD, OIDC, MFA, scoped RBAC, API tokens, HashiCorp Vault and SIEM
  forwarding.
- **Reports** as CSV or PDF, on demand or e-mailed on a schedule.

## What the chart deploys

| Component | Kind | Default replicas |
|---|---|---|
| API (stateless) | Deployment, Service, PodDisruptionBudget, optional HorizontalPodAutoscaler | 2 |
| Workers: collectors, alerting, schedules, backups; one is the scheduler leader | Deployment, PodDisruptionBudget | 2 |
| Frontend: nginx serving the UI and proxying `/api` | Deployment, Service, PodDisruptionBudget | 2 |
| PostgreSQL for the control plane, optional (bundled for evaluation) | StatefulSet | 1 |
| VictoriaMetrics for the monitoring history, optional (bundled) | StatefulSet | 1 |
| Ingress or OpenShift Route, NetworkPolicies, ServiceAccount, Secret | — | — |

Every pod meets the Kubernetes **restricted** Pod Security Standard. It runs non-root, without
privilege escalation, with all capabilities dropped, `RuntimeDefault` seccomp, no service-account
token, and a read-only root filesystem where the image allows it. `openshift=true` follows the
**restricted-v2** SCC. Images and chart are signed with Sigstore cosign and carry SBOM and SLSA
provenance attestations.

## Install

```bash
helm install dbm oci://ghcr.io/hbenabdalla/charts/dbmanager \
  --namespace dbmanager --create-namespace \
  --set secrets.masterKey="$(head -c32 /dev/urandom | base64)" \
  --set secrets.bootstrapAdminPassword='<a strong password>' \
  --set secrets.postgresPassword='<a strong password>' \
  --set secrets.timeseriesPassword='<a strong password>' \
  --set config.publicBaseUrl=https://dbmanager.example.com \
  --set ingress.enabled=true --set ingress.host=dbmanager.example.com
helm test dbm --namespace dbmanager
```

Sign in as `admin` with the bootstrap password. The **Platform Setup Wizard** then walks you
through the organization, sign-in methods and monitoring settings.

The install refuses to render when a required secret is missing; there are no default
passwords. **Back up `secrets.masterKey`.** Database credentials stored in DBManager are
encrypted with it and cannot be recovered without it.

### Production

```yaml
secrets:
  existingSecret: dbmanager-secrets   # DBM_MASTER_KEY, DBM_BOOTSTRAP_ADMIN_PASSWORD,
                                      # DBM_DATABASE_URL, DBM_TIMESERIES_PASSWORD
config:
  publicBaseUrl: https://dbmanager.example.com
  trustedProxies: "10.244.0.0/16"     # pod network of the frontend pods
frontend:
  outerProxy: "10.244.0.0/16"         # pod network of the Ingress controller
postgresql:
  bundled: { enabled: false }         # managed or operator-run PostgreSQL 15+
  tls:
    mode: verify-full
    caSecret: { name: pg-ca, key: ca.crt }
api:
  autoscaling: { enabled: true, minReplicas: 2, maxReplicas: 6 }
worker:
  replicas: 3
ingress:
  enabled: true
  className: nginx
  host: dbmanager.example.com
  annotations: { cert-manager.io/cluster-issuer: letsencrypt }
  tls: { enabled: true, secretName: dbmanager-tls }
```

### OpenShift

```bash
helm install dbm oci://ghcr.io/hbenabdalla/charts/dbmanager -f my-values.yaml \
  --set openshift=true --set route.enabled=true --set route.host=dbmanager.apps.example.com
```

## Main values

| Value | Default | Description |
|---|---|---|
| `secrets.existingSecret` | `""` | Secret with the keys listed above; otherwise the chart creates one from `secrets.*` |
| `secrets.masterKey` | `""` | base64 of 32 random bytes; encrypts stored credentials |
| `secrets.bootstrapAdminPassword` | `""` | first administrator, used once on an empty database |
| `secrets.databaseUrl` | `""` | external PostgreSQL: `postgresql+asyncpg://user:pass@host:5432/db` |
| `config.publicBaseUrl` | `https://dbmanager.example.com` | external URL (OIDC redirects, links in notifications) |
| `config.extraEnv` | `{}` | any other `DBM_*` setting |
| `api.replicas` / `worker.replicas` / `frontend.replicas` | `2` / `2` / `2` | replica counts |
| `api.autoscaling.enabled` | `false` | HorizontalPodAutoscaler for the API |
| `postgresql.bundled.enabled` | `true` | single-instance PostgreSQL for evaluation |
| `postgresql.tls.mode` | `""` | `require`, `verify-ca` or `verify-full` to the control-plane PostgreSQL |
| `timeseries.bundled.enabled` | `true` | single-node VictoriaMetrics; else `timeseries.external.url` |
| `ingress.enabled` / `route.enabled` | `false` / `false` | Ingress, or an OpenShift Route with edge TLS |
| `networkPolicy.enabled` | `true` | databases accept only DBManager pods; the API only the frontend |
| `openshift` | `false` | leave UID/GID/fsGroup to the restricted-v2 SCC |
| `image.backend.digest` / `image.frontend.digest` | `""` | pin a verified image digest (wins over the tag) |

`values.yaml` documents every value. `values.schema.json` rejects unknown or mistyped settings.

## Upgrade

```bash
helm upgrade dbm oci://ghcr.io/hbenabdalla/charts/dbmanager -n dbmanager --reuse-values
```

Each pod runs the database migrations at start, under a PostgreSQL advisory lock. Rolling
upgrades and many replicas starting at once are therefore safe. Back up first.

## Verify the images

```bash
cosign verify ghcr.io/hbenabdalla/dbmanager-backend:<version> \
  --certificate-identity-regexp 'https://github.com/hbenabdalla/dbmanager/.github/workflows/release.yml@refs/tags/v.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Uninstall

```bash
helm uninstall dbm -n dbmanager
```

The Secret the chart created is kept, so the master key is not lost. The volumes of the bundled
databases and the backup volume are kept too. Delete them yourself when you no longer need the
data.

## Links

- Documentation: <https://db-manager.readthedocs.io/en/latest/>
- Helm guide with every option:
  <https://db-manager.readthedocs.io/en/latest/deployment/kubernetes-helm/>
- High availability and internal TLS:
  <https://db-manager.readthedocs.io/en/latest/deployment/high-availability/>
- Source, issues and changelog: <https://github.com/hbenabdalla/dbmanager>

PostgreSQL, MySQL, MariaDB, SQL Server, Oracle and Db2 are trademarks of their respective
owners. DBManager is not affiliated with or endorsed by them.
