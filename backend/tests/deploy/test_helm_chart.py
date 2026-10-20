"""Helm chart (sprint 0.15.2/0.15.3): renders for every supported configuration, follows the
restricted Pod Security Standard (and OpenShift restricted-v2 in OpenShift mode), wires secrets,
TLS and replicas as documented, and refuses incomplete secret settings.

Needs the ``helm`` binary (present on GitHub-hosted runners); skipped without it. kubeconform
schema validation and the kind install run in .github/workflows/ci-kubernetes.yml."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from app import __version__

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / "deployment" / "helm" / "dbmanager"
HELM = shutil.which("helm")
pytestmark = pytest.mark.skipif(not HELM, reason="helm not installed")

_spec = importlib.util.spec_from_file_location("k8s_policy_check", ROOT / "scripts" / "k8s-policy-check.py")
assert _spec and _spec.loader
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)


def render(*values: str, sets: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    args = [HELM, "template", "t", str(CHART), "--kube-version", "1.31.0"]
    for v in values:
        args += ["-f", str(CHART / "ci" / f"{v}-values.yaml")]
    for s in sets:
        args += ["--set", s]
    out = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603 - fixed argv
    if out.returncode:
        raise RuntimeError(out.stderr.strip())
    return [d for d in yaml.safe_load_all(out.stdout) if isinstance(d, dict)]


def by(docs: list[dict[str, Any]], kind: str, suffix: str = "") -> dict[str, Any]:
    return next(d for d in docs if d["kind"] == kind and d["metadata"]["name"].endswith(suffix))


def env_of(container: dict[str, Any]) -> dict[str, Any]:
    return {e["name"]: e.get("value", e.get("valueFrom")) for e in container.get("env", [])}


def test_chart_version_follows_the_product() -> None:
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    assert chart["version"] == chart["appVersion"] == __version__


@pytest.mark.parametrize("values", ["default", "production", "openshift"])
def test_lint_and_restricted_pod_security(values: str) -> None:
    lint = subprocess.run(  # noqa: S603
        [HELM, "lint", str(CHART), "-f", str(CHART / "ci" / f"{values}-values.yaml"), "--kube-version", "1.31.0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert lint.returncode == 0, lint.stdout + lint.stderr
    docs = render(values)
    assert policy.check(docs, openshift=values == "openshift") == []


def test_default_install_bundles_stores_and_migrates_on_start() -> None:
    docs = render("default")
    kinds = sorted(d["kind"] for d in docs)
    assert kinds.count("StatefulSet") == 2 and kinds.count("Deployment") == 3
    cm = by(docs, "ConfigMap", "-config")["data"]
    assert cm["DBM_RUN_MIGRATIONS_ON_START"] == "true" and cm["DBM_RATE_LIMIT_BACKEND"] == "database"
    assert cm["DBM_TIMESERIES_URL"] == "http://t-dbmanager-timeseries:8428"
    secret = by(docs, "Secret")["stringData"]
    assert secret["DBM_DATABASE_URL"] == (
        "postgresql+asyncpg://dbmanager:ci-only-pg-password@t-dbmanager-postgresql:5432/dbmanager"
    )
    assert by(docs, "Secret")["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    api = by(docs, "Deployment", "-api")
    c = api["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == f"ghcr.io/hbenabdalla/dbmanager-backend:{__version__}"
    assert env_of(c)["DBM_PROCESS_ROLE"] == "api" and api["spec"]["replicas"] == 2
    assert "checksum/config" in api["spec"]["template"]["metadata"]["annotations"]
    worker = by(docs, "Deployment", "-worker")["spec"]["template"]["spec"]["containers"][0]
    assert worker["command"] == ["python", "-m", "app.worker"] and env_of(worker)["DBM_PROCESS_ROLE"] == "worker"
    web = by(docs, "Deployment", "-frontend")["spec"]["template"]["spec"]["containers"][0]
    assert (
        env_of(web)["BACKEND_URL"] == "http://t-dbmanager-api:8000" and env_of(web)["DBM_OUTER_PROXY"] == "10.0.0.0/8"
    )
    # PodDisruptionBudgets for the replicated tiers, NetworkPolicies for the data stores and the API
    assert {d["metadata"]["name"] for d in docs if d["kind"] == "PodDisruptionBudget"} == {
        "t-dbmanager-api",
        "t-dbmanager-worker",
        "t-dbmanager-frontend",
    }
    assert {d["metadata"]["name"] for d in docs if d["kind"] == "NetworkPolicy"} == {
        "t-dbmanager-postgresql",
        "t-dbmanager-timeseries",
        "t-dbmanager-api",
    }
    test = by(docs, "Pod", "-test")
    assert test["metadata"]["annotations"]["helm.sh/hook"] == "test"


def test_production_values_external_tls_existing_secret_hpa() -> None:
    docs = render("production")
    assert not [d for d in docs if d["kind"] == "StatefulSet"]
    assert not [d for d in docs if d["kind"] == "Secret"]  # existingSecret
    cm = by(docs, "ConfigMap", "-config")["data"]
    assert cm["DBM_DATABASE_TLS_MODE"] == "verify-full" and cm["DBM_TIMESERIES_URL"] == "https://vm.monitoring.svc:8428"
    api = by(docs, "Deployment", "-api")
    assert "replicas" not in api["spec"]  # the HPA owns it
    pod = api["spec"]["template"]["spec"]
    c = pod["containers"][0]
    env = env_of(c)
    assert env["DBM_DATABASE_TLS_CA_FILE"] == "/etc/dbmanager/tls/postgresql/ca.crt"
    assert env["DBM_TIMESERIES_TLS_CA_FILE"] == "/etc/dbmanager/tls/timeseries/ca.crt"
    assert env["DBM_MASTER_KEY"]["secretKeyRef"]["name"] == "dbmanager-secrets"
    assert c["image"].endswith("@sha256:" + "0" * 64)  # a digest wins over the tag
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["pg-ca"]["secret"]["secretName"] == "dbmanager-pg-ca"
    assert vols["backups"]["persistentVolumeClaim"]["claimName"] == "t-dbmanager-backups"
    assert by(docs, "HorizontalPodAutoscaler")["spec"]["maxReplicas"] == 5
    ing = by(docs, "Ingress")
    assert ing["spec"]["tls"][0]["hosts"] == ["dbmanager.example.com"] and ing["spec"]["ingressClassName"] == "nginx"
    assert by(docs, "Deployment", "-worker")["spec"]["replicas"] == 3
    np_api = by(docs, "NetworkPolicy", "-api")
    assert {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}}} in np_api["spec"][
        "ingress"
    ][0]["from"]


def test_openshift_mode_leaves_uids_to_the_scc_and_adds_a_route() -> None:
    docs = render("openshift")
    for d in docs:
        spec = policy.pod_spec(d)
        if spec:
            assert not {"runAsUser", "runAsGroup", "fsGroup"} & set(spec.get("securityContext") or {}), d["metadata"]
    route = by(docs, "Route")
    assert route["spec"]["tls"]["termination"] == "edge"


@pytest.mark.parametrize(
    ("sets", "message"),
    [
        ((), "secrets.masterKey is required"),
        (
            ("secrets.masterKey=" + "A" * 43 + "=", "secrets.timeseriesPassword=x"),
            "secrets.postgresPassword is required",
        ),
        (
            (
                "secrets.masterKey=" + "A" * 43 + "=",
                "secrets.timeseriesPassword=x",
                "postgresql.bundled.enabled=false",
            ),
            "secrets.databaseUrl is required",
        ),
        (("secrets.existingSecret=s", "timeseries.bundled.enabled=false"), "timeseries.external.url is required"),
        (("secrets.existingSecret=s", "config.extraEnv.FOO=1"), "DBM_"),
        (("secrets.existingSecret=s", "image.backend.tag=latest"), "image.backend.tag"),
    ],
)
def test_incomplete_settings_are_refused(sets: tuple[str, ...], message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        render(sets=sets)
