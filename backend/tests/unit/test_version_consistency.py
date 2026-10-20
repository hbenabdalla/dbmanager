"""One product version across the backend, its package metadata and the frontend."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from app import __version__

ROOT = Path(__file__).resolve().parents[3]


def _pep440(v: str) -> str:
    """``0.11.0-dev`` → ``0.11.0.dev0`` (the form Python packaging uses)."""
    return re.sub(r"-dev$", ".dev0", v)


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+(-(dev|rc\d+))?", __version__)


def test_pyproject_matches() -> None:
    meta = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text())
    assert meta["project"]["version"] == _pep440(__version__)


def test_frontend_matches() -> None:
    pkg = ROOT / "frontend" / "package.json"
    if not pkg.exists():
        pytest.skip("frontend not present in this checkout")
    assert json.loads(pkg.read_text())["version"] == __version__
    lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text())
    assert lock["version"] == __version__ and lock["packages"][""]["version"] == __version__


def test_chart_and_reference_manifests_match() -> None:
    chart = (ROOT / "deployment" / "helm" / "dbmanager" / "Chart.yaml").read_text()
    assert re.search(rf"^version: {re.escape(__version__)}$", chart, re.M)
    assert re.search(rf'^appVersion: "{re.escape(__version__)}"$', chart, re.M)
    for f in sorted((ROOT / "deployment" / "kubernetes").glob("*.yaml")):
        for tag in re.findall(r"ghcr\.io/hbenabdalla/dbmanager-(?:backend|frontend):(\S+)", f.read_text()):
            assert tag == __version__, f.name
