"""Integration-test plumbing: engine markers and the real-engine validation matrix."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.integration import matrix


def _engine(item: pytest.Item) -> str | None:
    callspec = getattr(item, "callspec", None)
    if callspec is not None and "flavor" in callspec.params:
        return str(callspec.params["flavor"])
    m = item.get_closest_marker("engine")
    if m and m.args:
        return str(m.args[0])
    return matrix.ENGINE_FILES.get(item.path.name)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):  # type: ignore[no-untyped-def]
    outcome = yield
    report = outcome.get_result()
    caps = [a for m in item.iter_markers("capability") for a in m.args]
    engine = _engine(item)
    if not caps or not engine:
        return
    if report.when == "call":
        result = "passed" if report.passed else ("skipped" if report.skipped else "failed")
    elif report.when == "setup" and (report.skipped or report.failed):
        result = "skipped" if report.skipped else "failed"
    else:
        return
    reason = None
    if report.skipped and isinstance(report.longrepr, tuple):
        reason = str(report.longrepr[2]).removeprefix("Skipped: ")
    for c in caps:
        matrix.record(engine, c, result, reason)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    data = matrix.build()
    if not data["engines"]:
        return
    out = Path(os.environ.get("DBM_IT_RESULTS_DIR", "integration-results"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "real-engine-matrix.json").write_text(json.dumps(data, indent=2))
    (out / "real-engine-matrix.md").write_text(matrix.markdown(data))
    target = os.environ.get("DBM_IT_MERGE_INTO")  # app/data/real_engine_validation.json
    if target:
        path = Path(target)
        existing = json.loads(path.read_text()) if path.exists() else None
        path.write_text(json.dumps(matrix.merge(existing, data), indent=2) + "\n")
