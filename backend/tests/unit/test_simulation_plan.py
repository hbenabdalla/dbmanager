"""Simulation planner: determinism, realism and coherence between condition and evidence."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.base import AdapterConnectionError, ConnectionSpec
from app.adapters.demo.adapter import DemoAdapter
from app.services.metric_catalog import compute_health
from app.simulation.generators.fleet import demo_profile, plan_fleet
from app.simulation.generators.metrics import sample_times
from app.simulation.profiles import PROFILES, SimulationProfile, get_profile, register_profile

NAME = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)*-(PROD|DR|UAT|DEV|TEST)-\d{2}$")


def _adapter(plan, seed: int, ts: datetime) -> DemoAdapter:  # type: ignore[no-untyped-def]
    prof = demo_profile(plan, seed, (ts - timedelta(hours=18)).isoformat())
    return DemoAdapter(ConnectionSpec(host=plan.host, port=plan.port, demo_profile=prof))


def test_profile_sizes() -> None:
    assert PROFILES["small"].servers == 5
    assert 15 <= PROFILES["standard"].servers <= 20
    assert 50 <= PROFILES["large"].servers <= 100
    assert PROFILES["large"].history_days > PROFILES["standard"].history_days > PROFILES["small"].history_days


def test_profiles_are_extensible() -> None:
    p = register_profile(
        SimulationProfile(
            key="tiny-test",
            label="Tiny",
            description="",
            environments={"Production": 1, "Development": 1},
            applications=2,
            history_days=1,
            query_history_hours=2,
            resolution=((24, 30),),
            include_cloud=False,
        )
    )
    assert get_profile("tiny-test") is p and len(plan_fleet(p, 1).servers) == 2
    del PROFILES["tiny-test"]
    with pytest.raises(KeyError):
        get_profile("tiny-test")


@pytest.mark.parametrize("key", ["small", "standard", "large"])
def test_same_seed_same_plan(key: str) -> None:
    a, b = plan_fleet(PROFILES[key], 42), plan_fleet(PROFILES[key], 42)
    assert [(s.name, s.scenarios, s.condition) for s in a.servers] == [
        (s.name, s.scenarios, s.condition) for s in b.servers
    ]
    c = plan_fleet(PROFILES[key], 43)
    assert [s.scenarios for s in a.servers] != [s.scenarios for s in c.servers]


@pytest.mark.parametrize("key", ["small", "standard", "large"])
def test_realistic_estate(key: str) -> None:
    plan = plan_fleet(PROFILES[key], 7)
    names = [s.name for s in plan.servers]
    assert len(names) == len(set(names)) == PROFILES[key].servers
    assert all(NAME.match(n) for n in names), names
    assert all(s.host.endswith(".sim.invalid") for s in plan.servers)  # never resolvable
    conditions = {s.condition for s in plan.servers}
    assert conditions == {"healthy", "warning", "critical", "unavailable"}
    if key != "small":
        assert {s.environment for s in plan.servers} == {"Production", "DR", "UAT", "Development", "Test"}
        assert {s.engine for s in plan.servers} == {"postgresql", "oracle", "db2", "mssql", "mysql", "mariadb"}
        assert plan.clusters and any(s.cluster_role == "standby" for s in plan.servers)
        assert any(s.deployment != "self-managed" for s in plan.servers)
    if key == "large":
        assert {s.deployment for s in plan.servers} >= {
            "aws-rds",
            "aws-aurora",
            "azure-sql",
            "oci-autonomous",
            "ibm-db2-cloud",
        }


@pytest.mark.parametrize("key", ["small", "standard", "large"])
def test_conditions_are_explained_by_the_metrics(key: str) -> None:
    """A server planned as critical/warning reports metrics that the health model rates that way."""
    peak = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
    for seed in (1, 2, 3):
        for s in plan_fleet(PROFILES[key], seed).servers:
            ad = _adapter(s, seed, peak)
            if s.condition == "unavailable":
                with pytest.raises(AdapterConnectionError):
                    import asyncio

                    asyncio.run(ad.connect())
                continue
            health, reasons = compute_health(ad.metrics_at(peak).gauges)
            if s.condition == "healthy":
                assert health in ("healthy", "warning"), (s.name, s.scenarios, reasons)
            else:
                assert health != "healthy", (s.name, s.condition, s.scenarios)
                assert reasons
                if s.condition == "critical":
                    assert health == "critical", (s.name, s.scenarios, reasons)


def test_regressing_query_gets_slower() -> None:
    plan = next(s for s in plan_fleet(PROFILES["standard"], 3).servers if "regressing_query" in s.scenarios)
    now = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
    ad = _adapter(plan, 3, now)
    early = ad.query_stats_at(now - timedelta(hours=17))
    late = ad.query_stats_at(now)
    from app.adapters.demo.catalog import QUERY_TEMPLATES

    hot = QUERY_TEMPLATES[plan.engine][0][0]  # the regressing statement
    m_early = next(q.mean_ms for q in early if q.query_text == hot)
    m_late = next(q.mean_ms for q in late if q.query_text == hot)
    assert m_late > m_early * 3


def test_sample_times_resolution() -> None:
    now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    ts = sample_times(PROFILES["standard"], now)
    assert ts[0] == now - timedelta(days=7) and ts[-1] <= now
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(ts, ts[1:], strict=False)]
    assert gaps[0] == 15 and gaps[-1] == 1
