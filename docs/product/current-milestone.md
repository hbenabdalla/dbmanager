# Current milestone

| | |
|---|---|
| **Milestone** | 0.15 — Cloud-Native Deployment |
| **Status** | DONE. Tag `v0.15.0` pushed but not published (release workflow failed before publishing); **0.15.1** fixed it and was the first published release; **0.15.3** adds the chart README (the `v0.15.2` tag was pushed before the version bump and published nothing) |
| **Release target** | 0.15.0 (this release). Next: milestone 1.0 — Production Release (**NEXT, not started**) |
| **Previous release** | 0.14.0 (published 2026-10-20) — reporting, branding, roadmap reconciliation |

## Objective

Run KBM DBManager on Kubernetes and OpenShift the way production platforms expect: installable with
one Helm command from signed images, several API and worker replicas that coordinate safely and
show who leads, encrypted connections to the control-plane databases, and every deployment
artefact validated in CI — including a real install with a leader failover.

## Scope

In scope: Helm chart; published, signed images with SBOM and provenance; manifest validation
(kubeconform, Pod Security, OpenShift restricted-v2, kind install); HA (multi-replica API, worker
leader failover, concurrent start); internal TLS (control-plane PostgreSQL, time-series store);
the deployment registry in the UI; dependency upgrades. Also in this release: the fixes of the
clean validation that preceded it ([test validation](test-validation.md)).

Out of scope (later milestones): an operator, a VictoriaMetrics cluster chart, cloud-provider
specific charts, a PostgreSQL HA chart (use a managed service or an operator).

## Carried over from the 0.14.0 validation

Fixed before any 0.15 feature work, each with a regression test ([details](test-validation.md#failures-and-root-causes)):

- the Compose restore drill (`scripts/backup.sh` used BusyBox `wget` options that do not exist);
- the MySQL 8.4 TLS test (detection through the removed `have_ssl`);
- TLS for MySQL and MariaDB in the Docker test environment (generated test CA);
- LDAP StartTLS against a server addressed by IP (ldap3 IP SAN handling).

## Sprints

| Sprint | Scope | Status |
|---|---|---|
| 0.15.1 Container images | `release.yml`: on a `vX.Y.Z` tag that matches the product and chart version and has release notes, images `ghcr.io/hbenabdalla/dbmanager-{backend,frontend}` tagged `X.Y.Z` and `X.Y`, OCI labels, SBOM and SLSA provenance attestations, keyless cosign signatures by digest, SPDX SBOM files on the GitHub release | DONE (runs on the tag) |
| 0.15.2 Helm chart | `deployment/helm/dbmanager`: API, worker and frontend Deployments, bundled or external PostgreSQL and VictoriaMetrics, Secret or existing Secret (required values, kept on uninstall), migrations on start under a lock, Ingress or OpenShift Route, PDBs, optional HPA, topology spread, NetworkPolicies, `helm test`, values schema; published as a signed OCI chart | DONE |
| 0.15.3 Manifest validation | `ci-kubernetes.yml`: `helm lint`, kubeconform (Kubernetes 1.29 and 1.31, strict), restricted Pod Security and OpenShift restricted-v2 checks (`scripts/k8s-policy-check.py`), chart tests, reference manifests; kind job: build images, install, `helm test`, sign-in through the frontend, delete the leader pod, another worker leads | DONE (kind job runs on push) |
| 0.15.4 HA & TLS reference | Advisory locks for migrations and start-up work (concurrent start fixed); instance registry (migration `0014`), heartbeat, `GET /api/v1/platform/instances`, Deployment card on **Platform → Collectors**, About summary, command palette entry; leader failover test with killed processes; `DBM_DATABASE_TLS_MODE` / `DBM_DATABASE_TLS_CA_FILE`, `DBM_TIMESERIES_TLS_CA_FILE`; CI runs the API suite against PostgreSQL with `verify-full`; [Helm](../deployment/kubernetes-helm.md) and [high availability](../deployment/high-availability.md) guides | DONE |
| 0.15.5 Dependency majors | Applied: Vite 8 (Rolldown) with plugin-react 6, Vitest 5, jest-dom 7, TanStack Query 5.104, lucide-react 0.577, Node 24 LTS for the build image and CI, nginx-unprivileged 1.31, mkdocs-material 9.7.7, GitHub Actions majors. Declined with reasons below | DONE |

## Decisions

* **Migrations on start, under a lock**, instead of a Helm hook Job: a hook Job runs before the
  Secret exists on the first install and blocks `--wait` installs; every replica starting with the
  lock is safe and needs no extra RBAC. Operators who prefer a Job set
  `DBM_RUN_MIGRATIONS_ON_START=false` and run `python -m app.cli migrate`.
* **Bundled PostgreSQL is for evaluation.** Production uses a managed or operator-run PostgreSQL;
  the chart does not try to be a PostgreSQL HA chart.
* **About shows counts only.** It is open to every signed-in user; host names and process ids stay
  on the Collectors page (`collectors.view`).
* **Keyless signing** (Sigstore, GitHub OIDC): no signing key to store or rotate; verification
  pins the release workflow identity.
* **Declined upgrades:**
    * Python 3.14 base image: the backend's binary dependencies (database drivers, ODBC, cryptography wheels) are validated on 3.12; revisit for 1.x.
    * Node 26 build image: not an LTS line; Node 24 LTS is used.
    * TypeScript 7: the native compiler is a new major whose project-reference build mode is not yet proven for this code base; stays on 5.9.
    * Recharts 3: API changes in every chart need a visual review of all monitoring pages; planned with the 1.0 UI review.
    * jsdom 30: its `Blob` is rejected by Node's `fetch` `Response` in tests ([#9](test-validation.md#failures-and-root-causes)); jsdom 26 stays.
* **Version 0.15.0** (semantic versioning, minor): new features, one additive migration (`0014`),
  no removed endpoints. The About response's `deployment` object is new.

## Dependencies

* Helm 3.8+ (OCI charts), Kubernetes 1.27+ or OpenShift 4.14+ (Kubernetes 1.27).
* GitHub Container Registry and Sigstore for publishing (release workflow only).
* PostgreSQL advisory locks (any supported PostgreSQL 15+).

## Acceptance criteria

| Criterion | Evidence |
|---|---|
| The chart renders for default, production and OpenShift values, passes lint, Kubernetes schemas (1.29, 1.31) and restricted Pod Security; OpenShift mode pins no UID | `tests/deploy/test_helm_chart.py::test_lint_and_restricted_pod_security`, `test_openshift_mode_leaves_uids_to_the_scc_and_adds_a_route`, `ci-kubernetes.yml` manifests job |
| Default install bundles the stores and migrates on start; production values use the external database with `verify-full`, an existing Secret and the HPA | `test_default_install_bundles_stores_and_migrates_on_start`, `test_production_values_external_tls_existing_secret_hpa` |
| Missing secrets are refused at render time | `test_incomplete_settings_are_refused` |
| Chart, reference manifests and product carry one version | `test_chart_version_follows_the_product`, `tests/unit/test_version_consistency.py` |
| Three replicas starting at once on an empty database migrate and bootstrap once | `tests/migrations/test_concurrent_start.py` |
| Killing the leader process moves the scheduler leadership to another worker | `tests/api/test_leader_failover.py`; on Kubernetes `scripts/k8s-failover-check.py` in the kind job |
| Every process is listed with role, version and heartbeat; crashed ones turn stale and are pruned; the list needs `collectors.view`; About shows no host names | `tests/api/test_instances.py` |
| The API suite runs against PostgreSQL with TLS `verify-full`; a wrong CA is refused | `tests/api/test_control_plane_tls.py`, `tests/unit/test_tlsclient.py` |
| Deployment card and About row in the UI | `frontend/src/test/deployment.test.tsx` |
| Upgrade from 0.14.0 keeps all data | `tests/migrations/test_migrations.py::test_existing_installation_upgrades_to_head_with_its_data[0.14.0]` |

## Next

Milestone **1.0 — Production Release** is NEXT and has not started: scale and security testing,
accessibility, upgrade drill from every 0.x schema, release candidate. See the [roadmap](roadmap.md).
