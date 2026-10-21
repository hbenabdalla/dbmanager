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


def test_powershell_integration_script_binds_every_engine_positionally(tmp_path: Path) -> None:
    """``integration-test.ps1 postgresql mysql …`` once bound ``postgresql`` to ``-K`` (the pytest
    -k filter): only PostgreSQL tests were selected, without the PostgreSQL host, and all skipped.
    Engines must be the only positional arguments."""
    import shutil
    import subprocess

    script = (ROOT / "scripts" / "integration-test.ps1").read_text(encoding="utf-8")
    assert re.search(r"\[CmdletBinding\(PositionalBinding\s*=\s*\$false\)\]\s*param\(", script)
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        return  # the static check above still guards the attribute
    # bind the script's own param block (nothing else of the script runs)
    block = script[script.index("[CmdletBinding") : script.index("$ErrorActionPreference")]
    probe = tmp_path / "bind.ps1"
    probe.write_text(block + "\n'E=' + ($Engines -join ','); 'K=' + $K\n", encoding="utf-8")
    cases = {
        ("postgresql", "mysql", "mariadb"): ("E=postgresql,mysql,mariadb", "K="),
        ("mssql", "oracle"): ("E=mssql,oracle", "K="),
        ("-K", "backup", "postgresql"): ("E=postgresql", "K=backup"),
    }
    for args, expected in cases.items():
        out = subprocess.run(  # noqa: S603 - fixed arguments
            [pwsh, "-NoProfile", "-NonInteractive", "-File", str(probe), *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout.split()
        assert tuple(out) == expected, (args, out)


def test_the_docker_runner_selects_every_integration_suite() -> None:
    """The runner image selects tests by marker; a suite without one of the selected markers was
    silently deselected (the VictoriaMetrics suite: 5 deselected although the engine was started)."""
    import subprocess
    import sys

    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    selected = re.search(r'"-m", "([^"]+)", "tests/integration"', dockerfile)
    assert selected, "runner CMD must select integration tests by marker"

    def collected(*extra: str) -> str:
        return (
            subprocess.run(  # noqa: S603 - fixed arguments
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "--collect-only",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    *extra,
                    "tests/integration",
                ],
                cwd=ROOT / "backend",
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            .stdout.strip()
            .splitlines()[-1]
            .split(" in ")[0]  # summary line without the timing
        )

    every = collected()
    assert "collected" in every and "deselected" not in every, every
    assert collected("-m", selected.group(1)) == every
