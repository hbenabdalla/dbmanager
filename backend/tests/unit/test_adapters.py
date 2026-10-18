from datetime import UTC, datetime

import pytest

from app.adapters.base import (
    AdapterConnectionError,
    AdapterNotSupported,
    BaseDatabaseAdapter,
    Capability,
    CapabilityLevel,
    ConnectionSpec,
)
from app.adapters.demo.adapter import DemoAdapter
from app.adapters.registry import adapter_class_for, supported_engines


def test_every_adapter_declares_every_capability() -> None:
    for name, cls in supported_engines().items():
        prof = cls.capability_profile()
        assert set(prof) == set(Capability), name
        assert set(cls.engine_profile()) == set(Capability), name


def test_planned_adapters_refuse_instead_of_faking() -> None:
    for engine, cls in supported_engines().items():
        if cls.implemented:
            continue
        assert all(not s.level.usable for s in cls.capability_profile().values()), engine


def test_implemented_adapters_declare_driver_and_support() -> None:
    from app.adapters import drivers

    for engine, cls in supported_engines().items():
        if not cls.implemented:
            continue
        assert cls.driver_key in drivers.DRIVERS, engine
        assert cls.support_level in ("supported", "partial"), engine
        assert cls.capability_profile()[Capability.CONNECTION].level.usable, engine


@pytest.mark.asyncio
async def test_planned_adapter_raises() -> None:
    """A planned engine (every capability PLANNED) never returns data: it raises instead."""
    from app.adapters.planned import _base, _planned

    class FutureAdapter(BaseDatabaseAdapter):
        engine = "future"
        display_name = "Future DB"
        default_port = 1
        implemented = False
        engine_capabilities = _base()
        static_capabilities = _planned(engine_capabilities)

    a = FutureAdapter(ConnectionSpec(host="h", port=1))
    assert not a.capability_profile()[Capability.SESSIONS].level.usable
    with pytest.raises(AdapterNotSupported):
        await a.get_sessions()


def test_oracle_profile_never_claims_pack_features() -> None:
    prof = adapter_class_for("oracle").engine_profile()
    note = prof[Capability.QUERY_HISTORY].note or ""
    assert prof[Capability.QUERY_HISTORY].level is CapabilityLevel.PARTIAL and "AWR" in note and "not read" in note
    assert "no ASH" in (prof[Capability.WAIT_EVENTS].note or "")


def _demo(**profile) -> DemoAdapter:  # type: ignore[no-untyped-def]
    p = {"engine": "postgresql", "seed": "x", "scale": 1.0, "scenarios": []}
    p.update(profile)
    return DemoAdapter(ConnectionSpec(host="x.demo", port=5432, demo_profile=p))


def test_demo_is_deterministic() -> None:
    ts = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert _demo().metrics_at(ts).gauges == _demo().metrics_at(ts).gauges
    assert _demo(seed="y").metrics_at(ts).gauges != _demo().metrics_at(ts).gauges


def test_demo_scenarios_shape_metrics() -> None:
    ts = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
    assert _demo(scenarios=["cache_pressure"]).metrics_at(ts).gauges["cache.hit_ratio"] < 95
    assert _demo(scenarios=["connection_saturation"], scale=1.5).metrics_at(ts).gauges["connections.utilization"] > 70


@pytest.mark.asyncio
async def test_demo_down_raises_connection_error() -> None:
    with pytest.raises(AdapterConnectionError):
        await _demo(scenarios=["down"]).get_performance_metrics()


@pytest.mark.asyncio
async def test_demo_mirrors_engine_capabilities() -> None:
    caps = await _demo(engine="db2").discover_capabilities()
    assert caps[Capability.DATABASE_CREATE].level is CapabilityLevel.NOT_SUPPORTED
    assert caps[Capability.SQL_WORKSPACE].level is CapabilityLevel.PARTIAL


@pytest.mark.asyncio
async def test_demo_never_executes_writes() -> None:
    res = await _demo().execute_sql("delete from t", None, read_only=False, max_rows=10, timeout_s=5)
    assert not res.committed and "not executed" in (res.status_message or "")
