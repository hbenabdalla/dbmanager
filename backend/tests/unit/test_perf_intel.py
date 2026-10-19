"""Performance intelligence (0.13): robust forecasting, baselines, regressions, period and plan
comparison, recommendation validation — against the in-memory time-series repository."""

from __future__ import annotations

import asyncio
import types
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.base import DatabaseInfo, QueryStat
from app.services import perf_intel as pi
from app.services.alerting import condition
from app.services.collection import size_samples
from app.timeseries.catalog import QUERY_BY_FIELD, name_for
from app.timeseries.memory import InMemoryTimeSeriesRepository
from app.timeseries.model import Sample
from app.timeseries.normalizer import normalize_query_stats

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
SID = str(uuid.UUID(int=7))
BASE = {"server_id": SID, "engine": "oracle", "data_source": "real"}


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def write(repo: InMemoryTimeSeriesRepository, samples: list[Sample]) -> None:
    run(repo.write_metrics_batch(samples))


# ---- Theil–Sen and forecasting ------------------------------------------------------------
def test_theil_sen_ignores_a_one_off_outlier() -> None:
    pts = [(float(d), 100.0 + 10 * d) for d in range(20)]
    pts[10] = (10.0, 5000.0)  # bulk load later purged: least squares would be dragged up
    fit = pi.theil_sen(pts)
    assert fit is not None
    assert fit["slope"] == pytest.approx(10.0, abs=1e-9)
    assert fit["slope_low"] <= fit["slope"] <= fit["slope_high"]


def test_forecast_days_to_full_with_band() -> None:
    pts = [(float(d), 1000.0 + 100 * d + (5 if d % 2 else -5)) for d in range(30)]
    f = pi.forecast(pts, capacity=10_000.0, horizon_days=120)
    assert f["growth_bytes_per_day"] == pytest.approx(100, rel=0.05)
    d = f["days_to_full"]
    # 1000 + 100·29 ≈ 3900 used → ≈ 61 days left at 100/day
    assert d["likely"] == pytest.approx(61, abs=2)
    assert d["earliest"] <= d["likely"] <= d["latest"]
    assert f["full_within_horizon"] is True
    assert f["band"][0]["low"] <= f["band"][0]["likely"] <= f["band"][0]["high"]
    assert all(b["low"] <= b["high"] for b in f["band"])


def test_forecast_refuses_thin_history_and_reports_why() -> None:
    f = pi.forecast([(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)], capacity=10.0)
    assert f["days_to_full"] is None and "insufficient history" in f["reason"]
    flat = pi.forecast([(float(d), 50.0) for d in range(10)], capacity=100.0)
    assert flat["days_to_full"] is None and flat["reason"] == "not growing"
    nocap = pi.forecast([(float(d), 50.0 + d) for d in range(10)], capacity=None)
    assert nocap["days_to_full"] is None and "no capacity" in nocap["reason"]


def test_capacity_forecast_per_database_and_tablespace() -> None:
    repo = InMemoryTimeSeriesRepository()
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    samples = []
    for d in range(15, -1, -1):
        ts = now - timedelta(days=d)
        samples += [
            Sample.make(name_for("storage.database_bytes"), BASE, 1000.0 + 50 * (15 - d), ts),
            Sample.make(name_for("storage.db_size_bytes"), {**BASE, "database": "SHOP"}, 400.0 + 40 * (15 - d), ts),
            Sample.make(name_for("storage.db_size_bytes"), {**BASE, "database": "HR"}, 100.0, ts),
            Sample.make(
                name_for("storage.tablespace_used_bytes"), {**BASE, "tablespace": "USERS"}, 500.0 + 20 * (15 - d), ts
            ),
        ]
    samples += [
        Sample.make(name_for("storage.capacity_bytes"), BASE, 5000.0, now),
        Sample.make(name_for("storage.tablespace_max_bytes"), {**BASE, "tablespace": "USERS"}, 1500.0, now),
    ]
    write(repo, samples)
    srv = run(pi.capacity_forecast([SID], "server", repo=repo))
    assert srv[0]["days_to_full"]["likely"] == pytest.approx((5000 - 1750) / 50, abs=2)
    dbs = {r["object"]: r for r in run(pi.capacity_forecast([SID], "database", repo=repo))}
    assert dbs["HR"]["reason"] == "not growing"
    # SHOP can grow into the server's free space: 5000 − 1750 = 3250 → capacity 1000 + 3250
    assert dbs["SHOP"]["capacity_bytes"] == pytest.approx(1000 + 3250)
    assert dbs["SHOP"]["days_to_full"]["likely"] == pytest.approx(3250 / 40, abs=2)
    ts = run(pi.capacity_forecast([SID], "tablespace", repo=repo))
    assert ts[0]["object"] == "USERS" and ts[0]["capacity_bytes"] == 1500
    assert ts[0]["days_to_full"]["likely"] == pytest.approx((1500 - 800) / 20, abs=2)


# ---- baselines and z-score alert rules ------------------------------------------------------
def _rule(**kw):  # type: ignore[no-untyped-def]
    d = {
        "kind": "metric",
        "metric": "cpu.percent",
        "operator": ">",
        "threshold": 3.0,
        "aggregation": "zscore",
        "window_seconds": 0,
        "baseline_days": 7,
        "name": "cpu anomaly",
    }
    d.update(kw)
    return types.SimpleNamespace(**d)


def test_zscore_needs_a_trustworthy_baseline() -> None:
    assert pi.zscore(None, {"avg": 1, "stddev": 1, "count": 100})[1] == "no current value"
    assert "needs 60 samples" in (pi.zscore(5, {"avg": 1, "stddev": 1, "count": 10})[1] or "")
    assert "no variation" in (pi.zscore(5, {"avg": 1, "stddev": 0, "count": 100})[1] or "")
    z, why = pi.zscore(40, {"avg": 20, "stddev": 5, "count": 100})
    assert z == 4 and why is None


def test_zscore_condition_on_latest_and_window_values() -> None:
    server = types.SimpleNamespace(
        id=SID,
        latest_metrics={"cpu.percent": 95, "_collected_at": NOW.isoformat()},
        collection_interval_s=60,
    )
    base = {"avg": 40.0, "stddev": 10.0, "count": 500}
    holds, z, desc = condition(_rule(), server, NOW, {SID: {"baseline": base, "current": None}})  # type: ignore[arg-type]
    assert holds is True and z == 5.5 and "baseline 40 ± 10 over 7d" in desc
    holds, z, _ = condition(_rule(window_seconds=600), server, NOW, {SID: {"baseline": base, "current": 45.0}})  # type: ignore[arg-type]
    assert holds is False and z == 0.5
    holds, z, desc = condition(_rule(), server, NOW, {SID: {"baseline": {"count": 3}, "current": None}})  # type: ignore[arg-type]
    assert holds is None and "baseline needs" in desc


def test_baselines_exclude_the_recent_window() -> None:
    repo = InMemoryTimeSeriesRepository()
    pi._BASELINE_CACHE.clear()
    pts = [
        Sample.make(name_for("cpu.percent"), BASE, 40.0 + (i % 5), NOW - timedelta(minutes=10 * i))
        for i in range(1, 300)
    ]
    pts.append(Sample.make(name_for("cpu.percent"), BASE, 99.0, NOW))
    write(repo, pts)
    b = run(pi.baselines("cpu.percent", [SID], days=7, exclude_recent_s=300, now=NOW, repo=repo))
    assert b[SID]["count"] >= 290 and 40 <= b[SID]["avg"] <= 44  # the spike at NOW is not in its own baseline


# ---- query regressions ----------------------------------------------------------------------
def _query_samples(fp: str, plans: list[tuple[datetime, float, float, int | None, int | None]]) -> list[Sample]:
    """Cumulative counters from (ts, calls, total_ms, plans, plan_id) points."""
    lbl = {**BASE, "database": "SHOP", "fingerprint": fp}
    out = []
    for ts, calls, total, n_plans, plan_id in plans:
        out.append(Sample.make(QUERY_BY_FIELD["calls"].name, lbl, calls, ts))
        out.append(Sample.make(QUERY_BY_FIELD["total_ms"].name, lbl, total, ts))
        if n_plans:
            out.append(Sample.make(QUERY_BY_FIELD["plans"].name, lbl, n_plans, ts))
        if plan_id:
            out.append(Sample.make(QUERY_BY_FIELD["plan_id"].name, lbl, plan_id, ts))
    return out


def _history(mean_before: float, mean_after: float, plan_before: int, plan_after: int) -> list:  # type: ignore[type-arg]
    pts, calls, total = [], 0.0, 0.0
    for h in range(48, -1, -1):
        ts = NOW - timedelta(hours=h)
        recent = h < 1
        calls += 100
        total += 100 * (mean_after if recent else mean_before)
        pts.append(
            (ts, calls, total, 2 if recent and plan_after != plan_before else 1, plan_after if recent else plan_before)
        )
    return pts


def test_regression_with_plan_change_is_detected() -> None:
    repo = InMemoryTimeSeriesRepository()
    write(repo, _query_samples("aaaa000000000001", _history(10.0, 40.0, 111, 222)))
    write(repo, _query_samples("aaaa000000000002", _history(10.0, 11.0, 333, 333)))  # +10 %: not a regression
    found = run(pi.query_regressions(SID, now=NOW, repo=repo))
    assert [f["fingerprint"] for f in found] == ["aaaa000000000001"]
    f = found[0]
    assert f["regressed"] and f["plan_changed"] and f["kind"] == "latency+plan"
    assert f["baseline_mean_ms"] == pytest.approx(10.0) and f["recent_mean_ms"] == pytest.approx(40.0)
    assert f["baseline_plan_id"] == "111" and f["recent_plan_id"] == "222" and f["recent_plans"] == 2


def test_regression_thresholds_suppress_noise() -> None:
    repo = InMemoryTimeSeriesRepository()
    write(repo, _query_samples("bbbb000000000001", _history(0.1, 0.5, 1, 1)))  # +400 % but only +0.4 ms
    assert run(pi.query_regressions(SID, now=NOW, repo=repo, min_delta_ms=5)) == []
    assert run(pi.query_regressions(SID, now=NOW, repo=repo, min_delta_ms=0.1))[0]["regressed"]
    assert run(pi.query_regressions(SID, now=NOW, repo=repo, min_delta_ms=0.1, min_calls=1000)) == []


def test_plan_change_without_regression_is_listed_but_not_regressed() -> None:
    repo = InMemoryTimeSeriesRepository()
    write(repo, _query_samples("cccc000000000001", _history(10.0, 9.0, 5, 6)))
    found = run(pi.query_regressions(SID, now=NOW, repo=repo))
    assert found[0]["kind"] == "plan_change" and not found[0]["regressed"]
    assert run(pi.query_regressions(SID, now=NOW, repo=repo, include_plan_only=False)) == []


def test_normalizer_keeps_plan_identity_of_the_dominant_statement() -> None:
    server = types.SimpleNamespace(id=SID, engine="oracle", data_source="real")
    stats = [
        QueryStat(
            query_id="a",
            query_text="SELECT * FROM t WHERE id = 1",
            database="X",
            calls=1,
            total_ms=5,
            mean_ms=5,
            plans=1,
            plan_id=11,
        ),
        QueryStat(
            query_id="b",
            query_text="SELECT * FROM t WHERE id = 2",
            database="X",
            calls=1,
            total_ms=50,
            mean_ms=50,
            plans=2,
            plan_id=22,
        ),
    ]
    samples, rows = normalize_query_stats(server, stats, NOW, top_n=10)
    by = {s.name: s.value for s in samples}
    assert by[QUERY_BY_FIELD["plan_id"].name] == 22 and by[QUERY_BY_FIELD["plans"].name] == 2
    assert by[QUERY_BY_FIELD["calls"].name] == 2 and len(rows) == 1


# ---- period comparison ----------------------------------------------------------------------
def test_compare_periods_classifies_statements() -> None:
    repo = InMemoryTimeSeriesRepository()
    lbl = lambda fp: {**BASE, "database": "SHOP", "fingerprint": fp}  # noqa: E731
    samples = []
    for h in range(0, 49):
        ts = NOW - timedelta(hours=48 - h)
        second_day = h > 24
        for fp, before, after in (("f1", 10.0, 30.0), ("f2", 10.0, 10.0)):
            n = 10 * h
            total = 10 * min(h, 24) * before + 10 * max(h - 24, 0) * after
            samples += [
                Sample.make(QUERY_BY_FIELD["calls"].name, lbl(fp), n, ts),
                Sample.make(QUERY_BY_FIELD["total_ms"].name, lbl(fp), total, ts),
            ]
        if second_day:
            samples += [
                Sample.make(QUERY_BY_FIELD["calls"].name, lbl("f3"), 5 * (h - 24), ts),
                Sample.make(QUERY_BY_FIELD["total_ms"].name, lbl("f3"), 50 * (h - 24), ts),
            ]
    write(repo, samples)
    a = (NOW - timedelta(hours=48), NOW - timedelta(hours=24))
    b = (NOW - timedelta(hours=24), NOW)
    out = run(pi.compare_periods(SID, a, b, repo=repo))
    st = {i["fingerprint"]: i for i in out["items"]}
    assert st["f1"]["status"] == "slower" and st["f1"]["mean_change_pct"] == pytest.approx(200, abs=10)
    assert st["f2"]["status"] == "similar"
    assert st["f3"]["status"] == "new" and st["f3"]["a"]["calls"] == 0
    assert out["items"][0]["fingerprint"] == "f1"  # ranked by the change of total time
    assert out["summary"]["b"]["total_ms"] > out["summary"]["a"]["total_ms"] and out["notes"] == []
    empty = run(pi.compare_periods(SID, (NOW - timedelta(days=30), NOW - timedelta(days=29)), b, repo=repo))
    assert empty["notes"] and {i["status"] for i in empty["items"]} == {"unclassified"}


# ---- plan comparison ------------------------------------------------------------------------
def _plan(op: str, index: str | None, cost: float, issues: list[str]) -> dict:  # type: ignore[type-arg]
    return {
        "engine": "postgresql",
        "mode": "estimate",
        "total_cost": cost,
        "root": {
            "id": 0,
            "operator": "Hash Join",
            "children": [
                {"id": 1, "operator": op, "relation": "orders", "index": index, "children": []},
                {"id": 2, "operator": "Seq Scan", "relation": "customers", "children": []},
            ],
        },
        "issues": [{"code": c, "title": c} for c in issues],
    }


def test_compare_plans_reports_access_path_changes() -> None:
    a = _plan("Seq Scan", None, 1000.0, ["SEQ_SCAN_LARGE"])
    b = _plan("Index Scan", "orders_customer_idx", 120.0, [])
    d = pi.compare_plans(a, b)
    assert d["cost_change_pct"] == -88.0 and not d["identical_shape"]
    assert d["operations_added"] == ["Index Scan on orders using orders_customer_idx"]
    assert d["operations_removed"] == ["Seq Scan on orders"]
    assert d["access_path_changes"] == [
        {"relation": "orders", "a": ["Seq Scan"], "b": ["Index Scan (orders_customer_idx)"]}
    ]
    assert d["issues_resolved"] == [{"code": "SEQ_SCAN_LARGE", "title": "SEQ_SCAN_LARGE"}]
    other = {**b, "engine": "oracle"}
    assert pi.compare_plans(a, other)["cost_change_pct"] is None


# ---- recommendation validation --------------------------------------------------------------
def test_validation_methods_and_verdicts() -> None:
    assert pi.validation_method("GEN-PERF-007", "query:abc")["fingerprint"] == "abc"  # type: ignore[index]
    assert pi.validation_method("GEN-PERF-002", None) == {
        "kind": "metric",
        "metric": "cache.hit_ratio",
        "better": "higher",
    }
    assert pi.validation_method("PG-PERF-003", "table:x") is None
    assert pi.verdict(100, 50, "lower") == ("improved", -50.0)
    assert pi.verdict(100, 105, "lower") == ("unchanged", 5.0)
    assert pi.verdict(90, 80, "higher") == ("regressed", -11.1)
    assert pi.verdict(None, 3, "lower")[0] == "insufficient_data"


def test_query_recommendation_validation_before_and_after() -> None:
    resolved = NOW - timedelta(hours=30)
    # after the fix (from `resolved` on) the statement is 5× faster
    lbl = {**BASE, "database": "SHOP", "fingerprint": "dddd000000000001"}
    calls, total, extra = 0.0, 0.0, []
    for h in range(48, -1, -1):
        ts = NOW - timedelta(hours=h)
        calls += 100
        total += 100 * (10.0 if ts > resolved else 50.0)
        extra += [
            Sample.make(QUERY_BY_FIELD["calls"].name, lbl, calls, ts),
            Sample.make(QUERY_BY_FIELD["total_ms"].name, lbl, total, ts),
        ]
    repo2 = InMemoryTimeSeriesRepository()
    write(repo2, extra)
    rec = types.SimpleNamespace(
        server_id=SID,
        rule_id="GEN-PERF-007",
        affected_object="query:dddd000000000001",
        database="SHOP",
        validation=None,
    )
    v = run(pi.start_validation(rec, resolved, 24, repo=repo2))
    assert v["status"] == "measuring" and v["before"]["value"] == pytest.approx(50.0)
    rec.validation = v
    assert run(pi.complete_validation(rec, resolved + timedelta(hours=1), repo=repo2)) is None  # not due
    done = run(pi.complete_validation(rec, NOW, repo=repo2))
    assert done is not None and done["status"] == "improved"
    assert done["after"]["value"] == pytest.approx(10.0, rel=0.05) and done["change_pct"] < -70


def test_unmeasurable_finding_is_marked_so() -> None:
    rec = types.SimpleNamespace(server_id=SID, rule_id="PG-PERF-003", affected_object=None, database=None)
    v = run(pi.start_validation(rec, NOW, 24, repo=InMemoryTimeSeriesRepository()))
    assert v["status"] == "not_measurable"


# ---- discovery size series ------------------------------------------------------------------
def test_size_samples_from_inventory_and_tablespaces() -> None:
    server = types.SimpleNamespace(id=SID, engine="oracle", data_source="real")
    dbs = [
        DatabaseInfo(name="SHOP", size_bytes=1000),
        DatabaseInfo(name="SYS", size_bytes=5, is_system=True),
        DatabaseInfo(name="EMPTY", size_bytes=None),
    ]
    ts = [
        {"name": "USERS", "used_bytes": 300, "max_bytes": 1000},
        {"name": "DB2TS", "used_bytes": 200, "allocated_bytes": 400, "autoextend": False},
        {"name": "AUTO", "used_bytes": 100, "allocated_bytes": 400, "autoextend": True},
        {"name": "pg_default", "size_bytes": 50},
        {"name": "nosize"},
    ]
    out = {
        (s.name, s.label_dict.get("database") or s.label_dict.get("tablespace")): s.value
        for s in size_samples(server, dbs, ts, NOW)
    }  # type: ignore[arg-type]
    assert out[("db_database_size_bytes", "SHOP")] == 1000
    assert ("db_database_size_bytes", "SYS") not in out and ("db_database_size_bytes", "EMPTY") not in out
    assert out[("db_tablespace_max_bytes", "USERS")] == 1000
    assert out[("db_tablespace_max_bytes", "DB2TS")] == 400
    assert ("db_tablespace_max_bytes", "AUTO") not in out
    assert out[("db_tablespace_used_bytes", "pg_default")] == 50
    assert all(k[1] != "nosize" for k in out)


def test_baseline_plan_when_the_change_happened_inside_the_baseline() -> None:
    assert pi._baseline_plan(5, 5, 5) == (5, False)
    assert pi._baseline_plan(5, 5, 7) == (5, True)
    assert pi._baseline_plan(5, 7, 7) == (5, True)  # switched to 7 during the baseline: 5 was the old plan
    assert pi._baseline_plan(5, 7, 5) == (7, True)
    assert pi._baseline_plan(None, None, 7) == (None, False)  # no plan identity in the baseline
