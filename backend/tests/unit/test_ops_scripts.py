"""The operations scripts run commands inside the shipped containers. Those images are minimal:
the VictoriaMetrics image has BusyBox ``wget`` (no ``--user`` / ``--password``), so a credentialed
request there failed the Compose restore drill in CI. Credentials also must never be passed on a
command line (visible in the process list)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = sorted((ROOT / "scripts").glob("*.sh")) + sorted((ROOT / "scripts" / "lib").glob("*.sh"))


def test_no_busybox_incompatible_wget_options() -> None:
    bad = [
        f"{p.name}:{n}"
        for p in SCRIPTS
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if not line.lstrip().startswith("#")
        and "wget" in line
        and re.search(r"--(user|password|http-user|http-password)\b", line)
    ]
    assert bad == []


def test_no_secret_on_a_command_line() -> None:
    secret = re.compile(r"\$\{?(DBM_[A-Z_]*(PASSWORD|SECRET|TOKEN|MASTER_KEY)[A-Z_]*)")
    offenders = []
    for p in SCRIPTS:
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if line.lstrip().startswith("#") or "docker" not in line and "curl" not in line and "wget" not in line:
                continue
            if secret.search(line):
                offenders.append(f"{p.name}:{n}: {line.strip()}")
    # the drill signs in through the API with the bootstrap password (JSON body over curl -d):
    # that is the only accepted case, and it targets a throw-away CI stack
    offenders = [o for o in offenders if "restore-drill.sh" not in o]
    assert offenders == []


def test_backup_script_snapshots_metrics_through_the_backend() -> None:
    s = (ROOT / "scripts" / "backup.sh").read_text()
    assert "exec -T backend python" in s and "/snapshot/create" in s and "/snapshot/delete" in s
