"""VictoriaMetrics repository against a real VictoriaMetrics single-node instance.

Enabled when ``DBM_IT_VM_URL`` is set, e.g.::

    DBM_IT_VM_URL=http://127.0.0.1:8428 DBM_IT_VM_USER=dbm DBM_IT_VM_PASSWORD=... pytest tests/integration

The instance must run with ``-dedup.minScrapeInterval=1ms`` (as in docker-compose).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services import metrics as svc
from app.timeseries.model import Sample, Selector
from app.timeseries.repository import TimeSeriesError, TimeSeriesUnavailable
from app.timeseries.victoriametrics import VictoriaMetricsRepository

URL = os.environ.get("DBM_IT_VM_URL")
pytestmark = [
    pytest.mark.skipif(not URL, reason="DBM_IT_VM_URL not set"),
    pytest.mark.asyncio(loop_scope="session"),
]
NOW = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)


def repo() -> VictoriaMetricsRepository:
    return VictoriaMetricsRepository(
        URL or "", os.environ.get("DBM_IT_VM_USER"), os.environ.get("DBM_IT_VM_PASSWORD"), timeout=10
    )


def lbl(sid: str, source: str = "real", **extra: str) -> dict[str, str]:
    return {"server_id": sid, "engine": "postgresql", "data_source": source, **extra}


async def visible(r: VictoriaMetricsRepository) -> None:
    await r.flush()
    await asyncio.sleep(1.5)  # search.latencyOffset / cache


async def test_write_read_aggregate_and_dedup() -> None:
    r = repo()
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    samples = []
    for i in range(30):
        ts = NOW - timedelta(minutes=29 - i)
        samples += [
            Sample.make("db_connections", lbl(a), float(i), ts),
            Sample.make("db_connections", lbl(b), 50.0, ts),
        ]
    assert await r.write_metrics_batch(samples) == 60
    await r.write_metrics_batch(samples)  # retry of the same batch
    await visible(r)
    sel = Selector.of("db_connections", [a, b])
    latest = {v.labels["server_id"]: (v.value, v.ts_ms) for v in await r.get_latest(sel, lookback_s=3600)}
    assert latest[a][0] == 29 and latest[b][0] == 50
    assert latest[a][1] == int(NOW.timestamp() * 1000)  # real sample timestamp, not query time
    cnt = await r.aggregate(Selector.of("db_connections", [a]), NOW - timedelta(minutes=40), NOW, "count")
    assert cnt[0].value == 30  # deduplicated
    avg = await r.aggregate(Selector.of("db_connections", [a]), NOW - timedelta(minutes=40), NOW, "avg")
    assert avg[0].value == pytest.approx(14.5)
    p95 = await r.aggregate(Selector.of("db_connections", [a]), NOW - timedelta(minutes=40), NOW, "p95")
    assert 26 <= p95[0].value <= 29
    rng = await r.query_range(sel, NOW - timedelta(minutes=20), NOW, 300, group_by=(), group_agg="sum")
    assert len(rng) == 1 and rng[0].labels.get("__name__") == "db_connections"
    assert rng[0].points[-1][1] == pytest.approx(29 + 50, abs=5)
    top = await r.top(sel, 1, lookback_s=3600)
    assert top[0].labels["server_id"] == b
    await r.close()


async def test_service_on_victoriametrics_counter_rate_and_series_shape() -> None:
    r = repo()
    sid = str(uuid.uuid4())
    await r.write_metrics_batch(
        [
            Sample.make("db_transactions_commits_total", lbl(sid), 60.0 * i, NOW - timedelta(minutes=10 - i))
            for i in range(11)
        ]
    )
    await visible(r)
    out = await svc.series([sid], ["transactions.commits"], NOW - timedelta(minutes=8), NOW, step=60, repo=r)
    vals = [p["v"] for p in out["series"][0]["points"]]
    assert vals and all(v == pytest.approx(1.0, abs=0.05) for v in vals)  # 60 per minute = 1/s
    await r.close()


async def test_delete_by_label_keeps_other_series() -> None:
    r = repo()
    real, sim = str(uuid.uuid4()), str(uuid.uuid4())
    await r.write_metrics_batch(
        [Sample.make("db_up", lbl(real), 1, NOW), Sample.make("db_up", lbl(sim, "simulated"), 1, NOW)]
    )
    await visible(r)
    with pytest.raises(TimeSeriesError):
        await r.delete_series({})
    await r.delete_series({"data_source": "simulated", "server_id": sim})
    await visible(r)
    left = {v.labels["server_id"] for v in await r.get_latest(Selector.of("db_up", [real, sim]), lookback_s=3600)}
    assert left == {real}
    await r.delete_server_metrics(real)
    await r.close()


async def test_status_and_health() -> None:
    r = repo()
    assert await r.health_check()
    st = await r.status()
    assert st.healthy and st.version and st.retention and st.series is not None
    assert st.details.get("deduplication") == "1ms"
    await r.close()


async def test_outage_and_bad_credentials_are_unavailable() -> None:
    down = VictoriaMetricsRepository("http://127.0.0.1:1", timeout=1)
    assert not await down.health_check()
    with pytest.raises(TimeSeriesUnavailable):
        await down.write_metrics_batch([Sample.make("db_up", lbl("x"), 1, NOW)])
    with pytest.raises(TimeSeriesUnavailable):
        await down.get_latest(Selector.of("db_up", ["x"]))
    st = await down.status()
    assert not st.healthy and st.error
    await down.close()
    if os.environ.get("DBM_IT_VM_PASSWORD"):
        wrong = VictoriaMetricsRepository(URL or "", "dbm", "wrong-password", timeout=3)
        with pytest.raises(TimeSeriesUnavailable):
            await wrong.get_latest(Selector.of("db_up", ["x"]))
        await wrong.close()
