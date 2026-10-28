# Kubernetes and OpenShift with Helm

[![Artifact Hub](https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/dbmanager)](https://artifacthub.io/packages/search?repo=dbmanager)

The Helm chart in `deployment/helm/dbmanager` is the supported way to run KBM DBManager on
Kubernetes (1.27 or later) and OpenShift (4.14 or later). It deploys:

| Component | Kind | Default replicas | Notes |
|---|---|---|---|
| API | Deployment + Service `<release>-dbmanager-api` | 2 | `DBM_PROCESS_ROLE=api`, stateless (sessions are in PostgreSQL); PodDisruptionBudget, optional HorizontalPodAutoscaler |
| Worker | Deployment | 2 | `python -m app.worker`: collectors, alert evaluation, scheduled reports and backups; one worker is the **scheduler leader** ([high availability](high-availability.md)) |
| Frontend | Deployment + Service `<release>-dbmanager` | 2 | unprivileged nginx serving the UI and proxying `/api` to the API Service; read-only root filesystem |
| PostgreSQL | StatefulSet (optional) | 1 | bundled single instance for evaluation; use a managed or operator-run HA PostgreSQL in production |
| VictoriaMetrics | StatefulSet (optional) | 1 | bundled single node for the monitoring history, or point at your own |
| Ingress or Route | optional | — | TLS at the edge |
| NetworkPolicy | on by default | — | PostgreSQL and VictoriaMetrics accept only KBM DBManager pods; the API only the frontend pods |

Every pod satisfies the Kubernetes **restricted** Pod Security Standard (non-root, no privilege
escalation, all capabilities dropped, `RuntimeDefault` seccomp, read-only root filesystem where
the image allows it, no service-account token mounted) and, with `openshift=true`, the OpenShift
**restricted-v2** SCC (no pinned UID, GID or fsGroup). CI checks this on every change
(`scripts/k8s-policy-check.py`, kubeconform against Kubernetes 1.29 and 1.31, and an install in a
kind cluster with `helm test` and a leader failover, see `.github/workflows/ci-kubernetes.yml`).

## Install

Released charts are published as signed OCI artifacts next to the images:

```bash
helm install dbm oci://ghcr.io/hbenabdalla/charts/dbmanager --version 0.16.0 \
  --namespace dbmanager --create-namespace \
  --set secrets.masterKey="$(head -c32 /dev/urandom | base64)" \
  --set secrets.bootstrapAdminPassword='<a strong password>' \
  --set secrets.postgresPassword='<a strong password>' \
  --set secrets.timeseriesPassword='<a strong password>' \
  --set config.publicBaseUrl=https://dbmanager.example.com \
  --set ingress.enabled=true --set ingress.host=dbmanager.example.com
helm test dbm --namespace dbmanager
```

The install refuses to render when a required secret is missing (no default passwords). **Back up
the master key**: credentials stored in KBM DBManager are encrypted with it and cannot be recovered
without it ([backup and restore](../operations/backup-restore.md)). The chart keeps the Secret it
created when the release is uninstalled (`helm.sh/resource-policy: keep`).

From a checkout, use the chart directory instead of the OCI reference
(`helm install dbm deployment/helm/dbmanager …`).

### Production values

```yaml
# production-values.yaml
secrets:
  existingSecret: dbmanager-secrets   # External Secrets / Sealed Secrets / Vault Agent …
config:
  publicBaseUrl: https://dbmanager.example.com
  trustedProxies: "10.244.0.0/16"     # pod network of the frontend pods
frontend:
  outerProxy: "10.244.0.0/16"         # pod network of the Ingress controller
postgresql:
  bundled: { enabled: false }         # DBM_DATABASE_URL comes from the existing Secret
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

The existing Secret holds the keys `DBM_MASTER_KEY`, `DBM_BOOTSTRAP_ADMIN_PASSWORD`,
`DBM_DATABASE_URL` and `DBM_TIMESERIES_PASSWORD` (plus `POSTGRES_PASSWORD` when the bundled
PostgreSQL is used). The full reference with comments is `values.yaml`; `values.schema.json`
rejects unknown or mistyped settings. `ci/production-values.yaml` and `ci/openshift-values.yaml`
are the value sets CI validates.

### OpenShift

```bash
helm install dbm oci://ghcr.io/hbenabdalla/charts/dbmanager --version 0.16.0 \
  -f my-values.yaml --set openshift=true \
  --set route.enabled=true --set route.host=dbmanager.apps.example.com
```

`openshift=true` leaves the UID, GID and fsGroup to the namespace's restricted-v2 SCC; the images
run under arbitrary UIDs (group 0 writable paths). The Route terminates TLS at the edge.

### Settings

Non-secret settings are `config.*` (mapped to `DBM_*`); any other setting of the
[environment reference](index.md#environment-variables) goes into `config.extraEnv`:

```yaml
config:
  extraEnv:
    DBM_QUERY_STATS_TOP_N: "30"
    DBM_INSTANCE_HEARTBEAT_SECONDS: "15"
```

## Migrations and upgrades

Every API and worker pod runs the database migrations at start (`DBM_RUN_MIGRATIONS_ON_START=true`)
under a PostgreSQL advisory lock: the first pod migrates, the others wait for it and then find the
schema current, so a rolling upgrade or many replicas starting at once is safe. To run migrations
yourself (for example a GitOps pre-sync Job), set
`config.extraEnv.DBM_RUN_MIGRATIONS_ON_START: "false"` and run `python -m app.cli migrate` with the
backend image before the upgrade.

```bash
helm upgrade dbm oci://ghcr.io/hbenabdalla/charts/dbmanager --version <new> -n dbmanager --reuse-values
```

Back up first ([backup and restore](../operations/backup-restore.md)). Mixed versions during a
rolling upgrade are shown on **Platform → Collectors → Deployment** until every pod runs the new
version.

## Images and signatures

Each release publishes, from `.github/workflows/release.yml`:

* `ghcr.io/hbenabdalla/dbmanager-backend:<version>` and `ghcr.io/hbenabdalla/dbmanager-frontend:<version>`
  (also tagged `<major>.<minor>`), with SBOM and SLSA provenance attestations in the registry;
* the chart `oci://ghcr.io/hbenabdalla/charts/dbmanager`;
* SPDX SBOM files attached to the GitHub release.

Images and the chart are signed keylessly with Sigstore cosign by the release workflow. Verify
before deploying:

```bash
cosign verify ghcr.io/hbenabdalla/dbmanager-backend:0.16.0 \
  --certificate-identity-regexp 'https://github.com/hbenabdalla/dbmanager/.github/workflows/release.yml@refs/tags/v.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The chart is listed on [Artifact Hub](https://artifacthub.io/) as a verified publisher: the
repository metadata `deployment/helm/artifacthub-repo.yml` is pushed to the chart repository's
`artifacthub.io` tag by `.github/workflows/artifacthub.yml`.

Pin the verified digest with `image.backend.digest` / `image.frontend.digest`: a digest wins over
the tag.

## Reference manifests

`deployment/kubernetes/` keeps plain manifests (with a migration Job instead of migrations on
start) for clusters where Helm is not used; they pass the same restricted Pod Security and schema
checks. The chart is the maintained path and has more options (TLS to the databases, HPA,
OpenShift mode, schema validation).

## Troubleshooting

| Symptom | Check |
|---|---|
| `helm install` fails with "secrets.… is required" | set the value or `secrets.existingSecret` |
| API pods not ready | `kubectl logs deploy/<release>-dbmanager-api`: database URL, TLS mode/CA, migration errors; `/readyz` reports which dependency fails |
| UI loads but sign-in fails behind the Ingress | `config.cookieSecure=true` requires HTTPS; `config.publicBaseUrl` must be the external URL |
| Audit log shows the ingress pod address | set `frontend.outerProxy` to the Ingress controller's pod network |
| No data collected | **Platform → Collectors → Deployment**: is there an active worker and a scheduler leader? ([high availability](high-availability.md#troubleshooting)) |
| Prometheus cannot scrape `/metrics` | port 8000 of the API pods and 8001 of the worker pods; the API NetworkPolicy admits only the frontend, so add the Prometheus namespace to `networkPolicy.extraApiIngressFrom` |
