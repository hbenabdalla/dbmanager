"""Time-series layer: label policy, naming, normalizer, writer, repository semantics and the
metrics service (against the in-memory repository, which mirrors VictoriaMetrics semantics)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.adapters.base import MetricSnapshot, QueryStat
from app.services import metrics as svc
from app.timeseries.catalog import QUERY_METRICS, IntervalClass, MetricType, all_definitions, name_for
from app.timeseries.memory import InMemoryTimeSeriesRepository
from app.timeseries.model import LabelPolicyError, Sample, Selector, check_labels
from app.timeseries.normalizer import fingerprint, normalize_query_stats, normalize_snapshot
from app.timeseries.repository import TimeSeriesError, TimeSeriesUnavailable
from app.timeseries.victoriametrics import rollup_expr, sample_line, selector_expr
from app.timeseries.writer import MetricWriter

NOW = datetime.now(UTC).replace(microsecond=0)


def srv(engine: str = "postgresql", source: str = "real") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), engine=engine, data_source=source)


def labels(s: SimpleNamespace, **extra: str) -> dict[str, str]:
    return {"server_id": str(s.id), "engine": s.engine, "data_source": s.data_source, **extra}


# ---- label policy & naming ----------------------------------------------------------------
def test_label_policy_rejects_unbounded_labels() -> None:
    s = srv()
    for bad in ("query", "sql", "session_id", "error", "environment", "username"):
        with pytest.raises(LabelPolicyError):
            check_labels({**labels(s), bad: "x"})
    with pytest.raises(LabelPolicyError):
        check_labels({"engine": "postgresql", "data_source": "real"})  # server_id missing
    with pytest.raises(LabelPolicyError):
        check_labels(labels(s, database="x" * 200))
    with pytest.raises(LabelPolicyError):
        check_labels(labels(s, database="a\nb"))
    check_labels(labels(s, database="app", fingerprint="0123456789abcdef", wait_type="IO"))


def test_samples_are_utc_only() -> None:
    s = srv()
    with pytest.raises(ValueError):
        Sample.make("db_up", labels(s), 1, datetime(2026, 1, 1, 12, 0))  # naive
    a = Sample.make("db_up", labels(s), 1, datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    b = Sample.make("db_up", labels(s), 1, datetime(2026, 1, 1, 13, 0, tzinfo=timedelta_tz(1)))
    assert a.ts_ms == b.ts_ms  # the same instant regardless of the offset it was given in


def timedelta_tz(hours: int):  # type: ignore[no-untyped-def]
    from datetime import timezone

    return timezone(timedelta(hours=hours))


def test_catalog_naming_convention() -> None:
    defs = all_definitions()
    names = [d.name for d in defs]
    assert len(names) == len(set(names))
    for d in defs:
        assert re.fullmatch(r"db_[a-z0-9_]+", d.name), d.name
        if d.type == MetricType.COUNTER:
            assert d.name.endswith("_total"), d.name
        elif d.type == MetricType.RATE:
            assert d.name.endswith("_per_second"), d.name
        else:
            assert not d.name.endswith("_total"), d.name
    assert name_for("connections.total") == "db_connections"
    assert name_for("pg.some.counter.per_sec") == "db_pg_some_counter_per_second"


# ---- normalizer ---------------------------------------------------------------------------
def test_normalize_snapshot_labels_counters_and_slow_class() -> None:
    s = srv()
    snap = MetricSnapshot(
        collected_at=NOW,
        gauges={"connections.total": 10, "storage.database_bytes": 1e9},
        counters={"transactions.commits": 500},
        labeled=[("wait.active_sessions", {"type": "IO"}, 3.0), ("wait.active_sessions", {"sql": "x"}, 1.0)],
    )
    state: dict = {}
    out = {
        (x.name, x.label_dict.get("wait_type")): x for x in normalize_snapshot(s, snap, {"cache.hit_ratio": 99}, state)
    }
    assert out[("db_connections", None)].value == 10
    assert out[("db_transactions_commits_total", None)].value == 500
    assert out[("db_wait_active_sessions", "IO")].value == 3.0
    assert ("db_wait_active_sessions", None) not in out  # unknown label key dropped, never stored
    assert ("db_storage_database_bytes", None) in out
    assert all(x.label_dict["data_source"] == "real" and x.label_dict["server_id"] == str(s.id) for x in out.values())
    # one minute later the slow-class storage gauge is not written again, fast metrics are
    snap2 = snap.model_copy(update={"collected_at": NOW + timedelta(minutes=1)})
    names2 = {x.name for x in normalize_snapshot(s, snap2, {}, state)}
    assert "db_connections" in names2 and "db_storage_database_bytes" not in names2
    snap3 = snap.model_copy(update={"collected_at": NOW + timedelta(minutes=6)})
    assert "db_storage_database_bytes" in {x.name for x in normalize_snapshot(s, snap3, {}, state)}


def q(text: str, calls: int, total: float, user: str = "app", db: str = "shop") -> QueryStat:
    return QueryStat(
        query_id=str(abs(hash(text)) % 10**9),
        query_text=text,
        database=db,
        username=user,
        calls=calls,
        total_ms=total,
        mean_ms=total / calls,
        max_ms=total / calls * 3,
        rows=calls,
    )


def test_fingerprint_ignores_literals_and_whitespace_but_not_engine() -> None:
    a = fingerprint("postgresql", "SELECT * FROM t WHERE id = 42")
    b = fingerprint("postgresql", "select *   from t\n where id = 7")
    assert a == b and len(a) == 16 and re.fullmatch(r"[0-9a-f]{16}", a)
    assert a != fingerprint("mysql", "SELECT * FROM t WHERE id = 42")
    assert a != fingerprint("postgresql", "SELECT * FROM u WHERE id = 42")


def test_query_stats_are_keyed_by_fingerprint_merged_and_capped() -> None:
    s = srv()
    stats = [
        q("SELECT * FROM orders WHERE id = 1", 10, 100, user="a"),
        q("SELECT * FROM orders WHERE id = 2", 30, 300, user="b"),  # same statement, other user
        q("UPDATE stock SET n = n - 1 WHERE sku = 'x'", 5, 1000),
        q("SELECT 1", 1, 0.1),
    ]
    samples, rows = normalize_query_stats(s, stats, NOW, top_n=10)
    assert len(rows) == 3
    for smp in samples:
        assert set(smp.label_dict) == {"server_id", "engine", "data_source", "database", "fingerprint"}
        assert "orders" not in "".join(v for _k, v in smp.labels)  # no SQL text in labels
    calls = {smp.label_dict["fingerprint"]: smp.value for smp in samples if smp.name == "db_query_calls_total"}
    fp_orders = fingerprint("postgresql", "SELECT * FROM orders WHERE id = 1")
    assert calls[fp_orders] == 40  # merged, one sample per series
    mean = next(x.value for x in samples if x.name == "db_query_mean_ms" and x.label_dict["fingerprint"] == fp_orders)
    assert mean == pytest.approx(10.0)
    assert "'x'" not in next(r.normalized_text for r in rows if "stock" in r.normalized_text)  # literals masked
    # top-N and the per-server fingerprint cap bound the cardinality
    _, rows = normalize_query_stats(s, stats, NOW, top_n=1)
    assert len(rows) == 1
    known = {"aaaaaaaaaaaaaaaa"}
    _, rows = normalize_query_stats(s, stats, NOW, top_n=10, known=known, max_fingerprints=2)
    assert len(rows) == 1
    assert {d.name for d in QUERY_METRICS} >= {"db_query_calls_total", "db_query_exec_time_ms_total"}


# ---- repository semantics -----------------------------------------------------------------
async def test_memory_repository_queries_and_dedup() -> None:
    repo = InMemoryTimeSeriesRepository()
    s1, s2 = srv(), srv("mysql")
    pts = []
    for i in range(60):
        ts = NOW - timedelta(minutes=59 - i)
        pts.append(Sample.make("db_connections", labels(s1), float(i), ts))
        pts.append(Sample.make("db_connections", labels(s2), 100.0, ts))
    await repo.write_metrics_batch(pts)
    await repo.write_metrics_batch(pts[:10])  # retried batch: deduplicated by (series, timestamp)
    assert (await repo.status()).samples == 120
    sel = Selector.of("db_connections", [str(s1.id), str(s2.id)])
    latest = {v.labels["server_id"]: v.value for v in await repo.get_latest(sel)}
    assert latest == {str(s1.id): 59.0, str(s2.id): 100.0}
    agg = {
        fn: (await repo.aggregate(Selector.of("db_connections", [str(s1.id)]), NOW - timedelta(hours=2), NOW, fn))[
            0
        ].value
        for fn in ("avg", "min", "max", "count", "sum", "p50", "p95", "p99")
    }
    assert agg["avg"] == pytest.approx(29.5) and agg["min"] == 0 and agg["max"] == 59 and agg["count"] == 60
    assert agg["sum"] == sum(range(60)) and agg["p50"] == pytest.approx(29.5, abs=1) and agg["p95"] >= 55
    grouped = await repo.query_range(sel, NOW - timedelta(minutes=30), NOW, 300, group_by=(), group_agg="sum")
    assert len(grouped) == 1 and grouped[0].points[-1][1] == pytest.approx(100 + 57, abs=3)
    # top-k and RBAC-style id restriction
    top = await repo.top(sel, 1)
    assert top[0].labels["server_id"] == str(s2.id)
    assert await repo.get_latest(Selector.of("db_connections", [])) == []


async def test_counter_rate_and_increase_handle_resets() -> None:
    repo = InMemoryTimeSeriesRepository()
    s = srv()
    values = [0, 60, 120, 180, 10, 70]  # reset after 180
    await repo.write_metrics_batch(
        [
            Sample.make("db_transactions_commits_total", labels(s), v, NOW - timedelta(minutes=5 - i))
            for i, v in enumerate(values)
        ]
    )
    sel = Selector.of("db_transactions_commits_total", [str(s.id)])
    inc = (await repo.aggregate(sel, NOW - timedelta(minutes=6), NOW, "increase"))[0].value
    assert inc == pytest.approx(180 + 10 + 60)
    out = await svc.series([s.id], ["transactions.commits"], NOW - timedelta(minutes=10), NOW, step=60, repo=repo)
    assert out["series"] and all(p["v"] >= 0 for p in out["series"][0]["points"])  # charted as a rate


async def test_delete_series_by_label_only() -> None:
    repo = InMemoryTimeSeriesRepository()
    real, sim = srv(), srv(source="simulated")
    await repo.write_metrics_batch(
        [Sample.make("db_up", labels(real), 1, NOW), Sample.make("db_up", labels(sim), 1, NOW)]
    )
    with pytest.raises(TimeSeriesError):
        await repo.delete_series({})
    await repo.delete_series({"data_source": "simulated"})
    left = await repo.get_latest(Selector.of("db_up", None))
    assert [v.labels["server_id"] for v in left] == [str(real.id)]
    await repo.delete_server_metrics(str(real.id))
    assert await repo.get_latest(Selector.of("db_up", None)) == []


# ---- writer -------------------------------------------------------------------------------
async def test_writer_batches_retries_and_bounds_its_buffer() -> None:
    repo = InMemoryTimeSeriesRepository()
    w = MetricWriter(repo, batch_size=10, flush_interval=60, buffer_max=25, max_backoff=1)
    s = srv()
    batch = [Sample.make("db_up", labels(s), 1, NOW - timedelta(seconds=i)) for i in range(20)]
    w.submit(batch)
    assert await w.flush_all() and repo.writes == 2 and w.written_total == 20 and w.pending == 0
    repo.available = False  # outage: nothing raises, samples stay buffered
    w.submit(batch)
    assert await w.flush_all() is False and w.pending == 20 and w.failed_batches == 1 and w.last_error
    w.submit(batch)  # 40 > buffer_max: the oldest are dropped and counted
    assert w.pending == 25 and w.dropped_total == 15
    with pytest.raises(TimeSeriesError):
        await w.flush_all(raise_on_failure=True)
    repo.available = True  # recovery: the backlog is written
    assert await w.flush_all() and w.pending == 0 and w.status()["last_success"] is not None


async def test_writer_background_loop_flushes() -> None:
    import asyncio

    repo = InMemoryTimeSeriesRepository()
    w = MetricWriter(repo, batch_size=5, flush_interval=0.05)
    w.start()
    w.submit([Sample.make("db_up", labels(srv()), 1, NOW)])
    await asyncio.sleep(0.2)
    assert w.written_total == 1 and w.status()["running"]
    await w.stop()
    assert not w.status()["running"]


# ---- metrics service ----------------------------------------------------------------------
def test_ranges_and_steps_bound_point_counts() -> None:
    for key, minutes in svc.RANGES.items():
        a, b = svc.resolve_range(key, None, None)
        assert (b - a) == timedelta(minutes=minutes) and a.tzinfo is not None
        assert (b - a).total_seconds() / svc.step_for(a, b) <= svc.TARGET_POINTS
    a, b = svc.resolve_range(None, datetime(2026, 1, 1), datetime(2026, 1, 2))  # naive → UTC
    assert a.tzinfo is UTC and b - a == timedelta(days=1)
    from app.core.errors import ValidationFailed

    with pytest.raises(ValidationFailed):
        svc.resolve_range("2h", None, None)
    with pytest.raises(ValidationFailed):
        svc.resolve_range(None, datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))


async def test_series_aggregation_modes() -> None:
    repo = InMemoryTimeSeriesRepository()
    s1, s2 = srv(), srv()
    for i in range(30):
        ts = NOW - timedelta(minutes=29 - i)
        await repo.write_metrics_batch(
            [Sample.make("db_connections", labels(s1), 10, ts), Sample.make("db_connections", labels(s2), 30, ts)]
        )
    start = NOW - timedelta(minutes=20)
    per = await svc.series([s1.id, s2.id], ["connections.total"], start, NOW, step=300, repo=repo)
    assert {x["server_id"] for x in per["series"]} == {str(s1.id), str(s2.id)}
    for mode, expect in (("sum", 40), ("avg", 20), ("max", 30)):
        out = await svc.series(
            [s1.id, s2.id], ["connections.total"], start, NOW, step=300, aggregate_servers=mode, repo=repo
        )  # type: ignore[arg-type]
        assert len(out["series"]) == 1 and out["series"][0]["points"][-1]["v"] == expect
    p95 = await svc.series([s1.id], ["connections.total"], start, NOW, step=300, agg="p95", repo=repo)
    assert p95["series"][0]["points"][-1]["v"] == 10
    # ids outside the caller's scope are simply not queried
    assert (await svc.series([], ["connections.total"], start, NOW, repo=repo))["series"] == []
    latest = await svc.latest([s1.id, s2.id], ["connections.total"], repo=repo)
    assert latest[str(s2.id)]["connections.total"] == 30 and "_collected_at" in latest[str(s1.id)]


async def test_sustained_condition_needs_every_sample_over_threshold() -> None:
    repo = InMemoryTimeSeriesRepository()
    s = srv()
    vals = [90, 91, 93, 95, 88, 92, 90, 89, 94, 96, 90, 91, 92, 90, 93]
    await repo.write_metrics_batch(
        [
            Sample.make("db_connections_utilization_percent", labels(s), v, NOW - timedelta(minutes=14 - i))
            for i, v in enumerate(vals)
        ]
    )
    r = await svc.sustained(s.id, "connections.utilization", 85, 15, repo=repo)
    assert r["sustained"] and r["bound"] == 88 and r["samples"] == 15
    await repo.write_metrics_batch(
        [Sample.make("db_connections_utilization_percent", labels(s), 50, NOW - timedelta(seconds=30))]
    )
    assert not (await svc.sustained(s.id, "connections.utilization", 85, 15, repo=repo))["sustained"]
    # too few samples never count as sustained
    s2 = srv()
    await repo.write_metrics_batch([Sample.make("db_connections_utilization_percent", labels(s2), 99, NOW)])
    assert not (await svc.sustained(s2.id, "connections.utilization", 85, 15, repo=repo))["sustained"]


async def test_baseline_and_capacity_refuse_thin_data() -> None:
    repo = InMemoryTimeSeriesRepository()
    s = srv()
    await repo.write_metrics_batch(
        [Sample.make("db_cpu_percent", labels(s), 40 + (i % 5), NOW - timedelta(minutes=i)) for i in range(10)]
    )
    b = await svc.baseline(s.id, "cpu.percent", 7, repo=repo)
    assert b["sufficient_data"] is False and "reason" in b and "mean" not in b
    await repo.write_metrics_batch(
        [Sample.make("db_cpu_percent", labels(s), 40 + (i % 5), NOW - timedelta(minutes=i)) for i in range(10, 200)]
    )
    await repo.write_metrics_batch([Sample.make("db_cpu_percent", labels(s), 90, NOW)])
    b = await svc.baseline(s.id, "cpu.percent", 7, repo=repo)
    assert b["sufficient_data"] and 41 < b["mean"] < 43 and b["z_score"] > 3 and b["anomalous"]

    # capacity: 3 days of history → no forecast; 20 days of steady growth → forecast
    s3 = srv()
    gb = 1024**3
    await repo.write_metrics_batch(
        [
            Sample.make("db_storage_database_bytes", labels(s3), 100 * gb + d * gb, NOW - timedelta(days=3 - d))
            for d in range(4)
        ]
        + [Sample.make("db_storage_capacity_bytes", labels(s3), 200 * gb, NOW)]
    )
    row = (await svc.capacity([s3.id], 30, repo=repo))[0]
    assert row["forecast"] is None and row["days_until_full"] is None and "insufficient" in row["reason"]
    s4 = srv()
    await repo.write_metrics_batch(
        [
            Sample.make("db_storage_database_bytes", labels(s4), 100 * gb + d * gb, NOW - timedelta(days=20 - d))
            for d in range(21)
        ]
        + [Sample.make("db_storage_capacity_bytes", labels(s4), 200 * gb, NOW)]
    )
    row = (await svc.capacity([s4.id], 30, repo=repo))[0]
    assert row["forecast"] == "linear" and row["growth_bytes_per_day"] == pytest.approx(gb, rel=0.05)
    assert row["days_until_full"] == pytest.approx(80, abs=3)


async def test_top_queries_rank_by_activity_in_window() -> None:
    repo = InMemoryTimeSeriesRepository()
    s = srv()
    busy = q("SELECT * FROM a WHERE x = 1", 1, 1)
    idle = q("SELECT * FROM b WHERE y = 1", 1, 1)
    samples = []
    for i in range(7):
        ts = NOW - timedelta(minutes=60 - 10 * i)
        busy2 = busy.model_copy(update={"calls": 100 * i, "total_ms": 1000.0 * i})
        idle2 = idle.model_copy(update={"calls": 1_000_000, "total_ms": 9e9})  # big but not moving
        smp, _ = normalize_query_stats(s, [busy2, idle2], ts, top_n=10)
        samples += smp
    await repo.write_metrics_batch(samples)
    top = await svc.top_queries([s.id], window_s=3600, sort="total_ms", limit=5, repo=repo)
    assert top[0]["fingerprint"] == fingerprint("postgresql", busy.query_text)
    assert top[0]["calls"] == pytest.approx(500, abs=100) and top[0]["mean_ms"] == pytest.approx(10)
    assert await svc.top_queries([], repo=repo) == []
    with pytest.raises(Exception):  # noqa: B017
        await svc.top_queries([s.id], sort="nope", repo=repo)


async def test_outage_raises_unavailable_for_reads() -> None:
    repo = InMemoryTimeSeriesRepository()
    repo.available = False
    with pytest.raises(TimeSeriesUnavailable):
        await svc.series([uuid.uuid4()], ["connections.total"], NOW - timedelta(hours=1), NOW, repo=repo)
    assert await repo.health_check() is False


# ---- VictoriaMetrics query building ------------------------------------------------------
def test_victoriametrics_selectors_escape_and_scope() -> None:
    sel = Selector.of(["db_up", "db_connections"], ["a.b", "c|d"], data_source="real", fingerprint=["x", "y"])
    expr = selector_expr(sel)
    assert expr is not None
    assert '__name__=~"db_up|db_connections"' in expr
    assert 'server_id=~"a\\\\.b|c\\\\|d"' in expr  # regex metacharacters escaped
    assert 'data_source="real"' in expr and 'fingerprint=~"x|y"' in expr
    assert selector_expr(Selector.of("db_up", [])) is None  # empty scope → no query at all
    assert selector_expr(Selector.of("db_up", None, database='a"b')) == '{__name__="db_up",database="a\\"b"}'
    assert rollup_expr("{x}", "p95", "1h") == "quantile_over_time(0.95, {x}[1h]) keep_metric_names"
    s = srv()
    line = sample_line(Sample.make("db_up", labels(s), 1, datetime(2026, 1, 1, tzinfo=UTC)))
    assert line.startswith("db_up{data_source=") and line.endswith(" 1.0 1767225600000")


def test_interval_classes_from_settings() -> None:
    from app.timeseries.normalizer import interval_seconds

    assert interval_seconds(IntervalClass.FAST) < interval_seconds(IntervalClass.NORMAL)
    assert interval_seconds(IntervalClass.SLOW) < interval_seconds(IntervalClass.VERY_SLOW)


def test_scheduler_jitter_is_stable_and_bounded() -> None:
    from app.jobs.handlers import jitter_for

    sid = uuid.uuid4()
    j = jitter_for("collect_metrics", sid, 60)
    assert j == jitter_for("collect_metrics", sid, 60) and 0 <= j < 12
    spread = {jitter_for("collect_metrics", uuid.uuid4(), 60) for _ in range(200)}
    assert len(spread) > 5  # a fleet is spread over the window, not synchronized


async def test_writer_discard_and_put_back_on_failure_keep_order() -> None:
    repo = InMemoryTimeSeriesRepository()
    w = MetricWriter(repo, batch_size=3, flush_interval=60, buffer_max=100)
    a, b = srv(), srv()
    w.submit([Sample.make("db_up", labels(x), i, NOW - timedelta(seconds=10 - i)) for i, x in enumerate([a, b, a, b])])
    assert w.discard({"server_id": [str(b.id)]}) == 2 and w.pending == 2
    repo.available = False
    assert not await w.flush_all() and [s.value for s in w._buf] == [0.0, 2.0]
    repo.available = True
    assert await w.flush_all() and w.pending == 0
