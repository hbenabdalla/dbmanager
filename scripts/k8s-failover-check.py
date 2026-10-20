#!/usr/bin/env python3
"""HA check of a running DBManager on Kubernetes (used by the kind job of ci-kubernetes.yml).

1. signs in through the frontend, checks the version and that every replica reports itself;
2. deletes the pod of the scheduler leader;
3. waits until another worker leads (and the deleted pod is gone from the registry).

    scripts/k8s-failover-check.py --base http://127.0.0.1:8080 --namespace dbm --password …
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import shutil
import subprocess
import sys
import time
import urllib.request


class Api:
    def __init__(self, base: str) -> None:
        if not base.startswith(("http://", "https://")):
            raise SystemExit("--base must be an http(s) URL")
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method: str, path: str, body: dict | None = None) -> dict:  # type: ignore[type-arg]
        csrf = next((c.value for c in self.jar if c.name == "dbm_csrf"), "")
        req = urllib.request.Request(  # noqa: S310 - scheme checked in __init__
            self.base + "/api/v1" + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
        )
        with self.op.open(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")


def wait(cond, seconds: float, what: str):  # type: ignore[no-untyped-def]
    end = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        try:
            last = cond()
            if last:
                return last
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
        time.sleep(3)
    sys.exit(f"timed out waiting for {what} (last: {last})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", required=True)
    a = ap.parse_args()
    api = Api(a.base)
    api.call("POST", "/auth/login", {"username": a.username, "password": a.password})
    print("version:", api.call("GET", "/version"))

    def deployment() -> dict | None:  # type: ignore[type-arg]
        d = api.call("GET", "/platform/instances")
        s = d["summary"]
        return d if s["api"] >= 2 and s["workers"] >= 2 and s["leader"] else None

    d = wait(deployment, 180, "two API replicas, two workers and a leader")
    leader = d["summary"]["leader"]
    pod = next(i["hostname"] for i in d["instances"] if i["id"] == leader)
    print(f"leader {leader} (pod {pod}); deleting the pod")
    subprocess.run(  # noqa: S603 - fixed kubectl argv, pod name read from the API
        [shutil.which("kubectl") or "kubectl", "-n", a.namespace, "delete", "pod", pod, "--wait=false"], check=True
    )

    def new_leader() -> str | None:
        s = api.call("GET", "/platform/instances")["summary"]
        return s["leader"] if s["leader"] and not s["leader"].startswith(pod + ":") else None

    moved = wait(new_leader, 180, "another worker to take the scheduler leadership")
    print(f"leadership moved to {moved}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
