from app.services.metric_catalog import compute_health, derive


def test_derive_rates_and_ratios() -> None:
    prev = {
        "ts": 100.0,
        "counters": {
            "transactions.commits": 1000,
            "transactions.rollbacks": 10,
            "io.blocks_hit": 9000,
            "io.blocks_read": 1000,
            "query.calls": 500,
            "query.total_ms": 5000,
            "deadlocks": 2,
        },
    }
    now = {
        "transactions.commits": 1600,
        "transactions.rollbacks": 30,
        "io.blocks_hit": 18500,
        "io.blocks_read": 1500,
        "query.calls": 1500,
        "query.total_ms": 25000,
        "deadlocks": 3,
    }
    out, state = derive(now, prev, 160.0)
    assert out["commits.per_sec"] == 10.0
    assert out["transactions.per_sec"] == round(10.0 + 20 / 60, 4)
    assert round(out["cache.hit_ratio"], 2) == 95.0
    assert out["query.mean_latency_ms"] == 20.0
    assert out["deadlocks.count"] == 1
    assert state["counters"] == now


def test_counter_reset_is_ignored() -> None:
    out, _ = derive({"transactions.commits": 5}, {"ts": 0.0, "counters": {"transactions.commits": 100}}, 60.0)
    assert "commits.per_sec" not in out


def test_first_snapshot_has_no_rates() -> None:
    out, state = derive({"transactions.commits": 5}, None, 60.0)
    assert out == {} and state["ts"] == 60.0


def test_health() -> None:
    assert compute_health({"availability.up": 0})[0] == "unavailable"
    assert compute_health({"connections.utilization": 97})[0] == "critical"
    assert compute_health({"connections.utilization": 85})[0] == "warning"
    assert compute_health({"replication.lag_seconds": 400})[0] == "critical"
    assert compute_health({"connections.utilization": 20, "cache.hit_ratio": 99.5})[0] == "healthy"
