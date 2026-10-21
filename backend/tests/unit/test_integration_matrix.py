"""Merging a real-engine run into the shipped validation file (backend/app/data/real_engine_validation.json)."""

from __future__ import annotations

from tests.integration.matrix import merge

OLD = {
    "generated_at": "2026-10-20T07:26:43+00:00",
    "engines": {"postgresql": {"result": "passed", "version": "16.15"}},
}


def test_an_all_skipped_run_changes_neither_results_nor_the_date() -> None:
    new = {"generated_at": "2026-10-21T09:16:36+00:00", "engines": {"postgresql": {"result": "skipped"}}}
    out = merge(OLD, new)
    assert out["engines"]["postgresql"]["result"] == "passed"
    assert out["generated_at"] == OLD["generated_at"]


def test_an_exercised_engine_replaces_its_result_and_dates_the_file() -> None:
    new = {"generated_at": "2026-10-21T09:16:36+00:00", "engines": {"postgresql": {"result": "failed"}}}
    out = merge(OLD, new)
    assert out["engines"]["postgresql"]["result"] == "failed"
    assert out["engines"]["postgresql"]["validated_at"] == new["generated_at"]
    assert out["generated_at"] == new["generated_at"]
