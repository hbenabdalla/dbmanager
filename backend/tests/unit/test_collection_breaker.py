"""Collector circuit breaker: after ``circuit_failure_threshold`` consecutive failures a server
is skipped for 60·2ⁿ seconds (capped at one hour); one success closes the circuit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.core.config import get_settings
from app.services.collection import mark_failure, mark_success


def _server() -> SimpleNamespace:
    return SimpleNamespace(
        consecutive_failures=0,
        circuit_open_until=None,
        connection_status="unknown",
        last_error=None,
        last_connected_at=None,
    )


def _open_for(s: SimpleNamespace) -> float:
    assert s.circuit_open_until is not None
    return (s.circuit_open_until - datetime.now(UTC)).total_seconds()


def test_circuit_opens_after_the_threshold_with_exponential_backoff() -> None:
    threshold = get_settings().circuit_failure_threshold
    s = _server()
    for _ in range(threshold - 1):
        mark_failure(s, "connection refused")
        assert s.circuit_open_until is None and s.connection_status == "failed"
    mark_failure(s, "connection refused")
    assert 55 <= _open_for(s) <= 60
    mark_failure(s, "connection refused")
    assert 115 <= _open_for(s) <= 120
    for _ in range(10):
        mark_failure(s, "x" * 5000)
    assert 3595 <= _open_for(s) <= 3600  # capped at one hour
    assert len(s.last_error) == 2000  # long driver messages are cut


def test_one_success_closes_the_circuit() -> None:
    s = _server()
    for _ in range(get_settings().circuit_failure_threshold + 2):
        mark_failure(s, "timeout")
    mark_success(s)
    assert s.consecutive_failures == 0 and s.circuit_open_until is None and s.connection_status == "ok"
    assert s.last_error is None and s.last_connected_at > datetime.now(UTC) - timedelta(seconds=5)
