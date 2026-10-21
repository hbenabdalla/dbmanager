"""Real-engine validation matrix.

Integration tests mark what they validate with ``@pytest.mark.capability("sessions", ...)``
and record the server version with :func:`record_version`. When the run ends,
``tests/integration/conftest.py`` writes ``real-engine-matrix.json`` and ``.md`` to
``DBM_IT_RESULTS_DIR`` (default ``integration-results/``). ``scripts/integration-test.*``
copies the JSON to ``app/data/real_engine_validation.json``, which the Engine Support page shows.

A capability counts as validated for an engine only when at least one test for it passed
against a real server and none failed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

CAPABILITIES = [
    "connection",
    "tls",
    "inventory",
    "users",
    "privileges",
    "sessions",
    "locks",
    "metrics",
    "queries",
    "explain",
    "storage",
    "security",
    "recommendations",
    "administration",
    # integrations (identity providers, secret stores) and backup/restore
    "authentication",
    "group_mapping",
    "mfa",
    "secrets",
    "backup",
    "restore",
]
ENGINE_FILES = {
    "test_postgres_adapter.py": "postgresql",
    "test_mssql.py": "mssql",
    "test_oracle.py": "oracle",
    "test_db2.py": "db2",
    "test_victoriametrics.py": "timeseries",
}

_versions: dict[str, str] = {}
_results: dict[str, dict[str, dict[str, Any]]] = {}


def record_version(engine: str, version: str | None) -> None:
    if version:
        _versions[engine] = str(version)


def record(engine: str, capability: str, outcome: str, reason: str | None = None) -> None:
    cell = _results.setdefault(engine, {}).setdefault(
        capability, {"passed": 0, "failed": 0, "skipped": 0, "skip_reasons": []}
    )
    cell[outcome] += 1
    if outcome == "skipped" and reason and reason not in cell["skip_reasons"]:
        cell["skip_reasons"].append(reason[:300])


def status(cell: dict[str, Any] | None) -> str:
    if not cell:
        return "not_tested"
    if cell["failed"]:
        return "failed"
    if cell["passed"]:
        return "validated"
    return "skipped"


def build() -> dict[str, Any]:
    engines = {}
    for engine, caps in _results.items():
        engines[engine] = {
            "version": _versions.get(engine),
            "capabilities": {c: {"status": status(caps.get(c)), **(caps.get(c) or {})} for c in CAPABILITIES},
            "result": "failed"
            if any(status(v) == "failed" for v in caps.values())
            else ("passed" if any(status(v) == "validated" for v in caps.values()) else "skipped"),
        }
    return {"generated_at": datetime.now(UTC).isoformat(), "capabilities": CAPABILITIES, "engines": engines}


def markdown(data: dict[str, Any]) -> str:
    mark = {"validated": "PASS", "failed": "FAIL", "skipped": "skip", "not_tested": "—"}
    lines = [
        f"# Real-engine validation matrix ({data['generated_at'][:19]}Z)",
        "",
        "| Engine | Version | " + " | ".join(c.title() for c in data["capabilities"]) + " | Result |",
        "|---|---|" + "---|" * (len(data["capabilities"]) + 1),
    ]
    for engine, e in sorted(data["engines"].items()):
        cells = [mark[e["capabilities"][c]["status"]] for c in data["capabilities"]]
        lines.append(f"| {engine} | {e['version'] or '?'} | " + " | ".join(cells) + f" | {e['result'].upper()} |")
    skipped = [
        f"* {engine} / {c}: {'; '.join(v['skip_reasons'])}"
        for engine, e in sorted(data["engines"].items())
        for c, v in e["capabilities"].items()
        if v["status"] == "skipped" and v.get("skip_reasons")
    ]
    if skipped:
        lines += ["", "Skipped:", *skipped]
    return "\n".join(lines) + "\n"


def merge(existing: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """Merge a run into the shipped validation file: an engine is replaced only when this run
    actually exercised it (passed or failed); engines whose suites were all skipped keep their
    earlier validated result instead of being downgraded."""
    out = {"capabilities": CAPABILITIES, "engines": dict((existing or {}).get("engines", {}))}
    exercised = False
    for engine, e in new["engines"].items():
        if e["result"] != "skipped" or engine not in out["engines"]:
            out["engines"][engine] = {**e, "validated_at": new["generated_at"]}
            exercised = exercised or e["result"] != "skipped"
    if existing and "environment" in existing:
        out["environment"] = existing["environment"]
    for key in ("pending", "notes"):
        if existing and key in existing:
            out[key] = (
                {
                    k: v
                    for k, v in existing[key].items()
                    if k not in new["engines"] or new["engines"][k]["result"] == "skipped"
                }
                if isinstance(existing[key], dict)
                else existing[key]
            )
    # a run that exercised nothing (every suite skipped) does not make the file look newer
    out["generated_at"] = new["generated_at"] if exercised or not existing else existing.get("generated_at")
    return out
