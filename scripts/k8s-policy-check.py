#!/usr/bin/env python3
"""Check rendered Kubernetes manifests against the *restricted* Pod Security Standard and the
OpenShift restricted-v2 SCC (sprint 0.15.3).

    helm template dbm deployment/helm/dbmanager -f values.yaml | scripts/k8s-policy-check.py [--openshift]

Exit code 1 lists every violation. ``--openshift``: additionally no pod may pin runAsUser,
runAsGroup or fsGroup (restricted-v2 assigns them from the namespace range)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod"}


def pod_spec(doc: dict[str, Any]) -> dict[str, Any] | None:
    kind = doc.get("kind")
    if kind not in POD_KINDS:
        return None
    if kind == "Pod":
        return doc.get("spec") or {}
    if kind == "CronJob":
        return doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return doc["spec"]["template"]["spec"]


def check(docs: list[dict[str, Any]], openshift: bool = False) -> list[str]:
    problems: list[str] = []
    for doc in docs:
        spec = pod_spec(doc)
        if spec is None:
            continue
        name = f"{doc['kind']}/{doc['metadata']['name']}"
        psc = spec.get("securityContext") or {}
        if spec.get("hostNetwork") or spec.get("hostPID") or spec.get("hostIPC"):
            problems.append(f"{name}: host namespaces")
        for v in spec.get("volumes") or []:
            if "hostPath" in v:
                problems.append(f"{name}: hostPath volume {v['name']}")
        if spec.get("automountServiceAccountToken") is not False:
            problems.append(f"{name}: automountServiceAccountToken must be false")
        if openshift:
            for k in ("runAsUser", "runAsGroup", "fsGroup"):
                if k in psc:
                    problems.append(f"{name}: {k} pinned (OpenShift restricted-v2 assigns it)")
        for c in (spec.get("initContainers") or []) + (spec.get("containers") or []):
            cname = f"{name}/{c['name']}"
            csc = c.get("securityContext") or {}
            run_as_non_root = csc.get("runAsNonRoot", psc.get("runAsNonRoot"))
            if run_as_non_root is not True:
                problems.append(f"{cname}: runAsNonRoot must be true")
            if csc.get("runAsUser", psc.get("runAsUser")) == 0:
                problems.append(f"{cname}: runs as uid 0")
            seccomp = (csc.get("seccompProfile") or psc.get("seccompProfile") or {}).get("type")
            if seccomp not in ("RuntimeDefault", "Localhost"):
                problems.append(f"{cname}: seccompProfile must be RuntimeDefault")
            if csc.get("allowPrivilegeEscalation") is not False:
                problems.append(f"{cname}: allowPrivilegeEscalation must be false")
            if csc.get("privileged"):
                problems.append(f"{cname}: privileged")
            caps = csc.get("capabilities") or {}
            if "ALL" not in (caps.get("drop") or []):
                problems.append(f"{cname}: capabilities must drop ALL")
            if set(caps.get("add") or []) - {"NET_BIND_SERVICE"}:
                problems.append(f"{cname}: adds capabilities {caps.get('add')}")
            if csc.get("readOnlyRootFilesystem") is not True:
                problems.append(f"{cname}: readOnlyRootFilesystem should be true")
            image = str(c.get("image", ""))
            if image.endswith(":latest") or (":" not in image.rsplit("/", 1)[-1] and "@" not in image):
                problems.append(f"{cname}: image {image!r} is not pinned to a tag or digest")
            if doc["kind"] != "Pod" and not (c.get("resources") or {}).get("requests"):
                problems.append(f"{cname}: no resource requests")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--openshift", action="store_true")
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    text = "".join(Path(f).read_text() + "\n---\n" for f in a.files) if a.files else sys.stdin.read()
    docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    problems = check(docs, a.openshift)
    for p in problems:
        print(f"POLICY: {p}")
    print(f"{len(docs)} manifests checked, {len(problems)} violation(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
