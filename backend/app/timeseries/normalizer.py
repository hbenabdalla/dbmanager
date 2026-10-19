"""Metric normalizer: vendor-specific adapter output → the platform's common metric model.

Adapters return a :class:`MetricSnapshot` with gauges, cumulative counters and labeled values
in platform metric keys. The normalizer turns them into storage samples with the fixed label
set (``server_id``, ``engine``, ``data_source`` and only bounded extra labels), applies
collection-class throttling, and turns query statistics into fingerprinted query telemetry.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.adapters.base import MetricSnapshot, QueryStat
from app.core.metrics import METRICS_DROPPED
from app.domain.masking import mask_sql
from app.timeseries.catalog import BY_KEY, QUERY_METRICS, IntervalClass, name_for
from app.timeseries.model import LabelPolicyError, Sample

log = logging.getLogger(__name__)

#: adapter label key → storage label key (only bounded keys are accepted)
LABEL_MAP = {"type": "wait_type", "wait_type": "wait_type", "database": "database", "tablespace": "tablespace"}
_WS = re.compile(r"\s+")


def base_labels(server: Any) -> dict[str, str]:
    """Stable identifiers only. Inventory attributes (environment, site, application …) are
    deliberately not labels: they live in PostgreSQL and are joined at query time."""
    return {"server_id": str(server.id), "engine": server.engine, "data_source": server.data_source}


def counter_name(key: str) -> str:
    d = BY_KEY.get(key)
    if d:
        return d.name
    n = name_for(key)
    return n if n.endswith("_total") else f"{n}_total"


def interval_seconds(cls: IntervalClass) -> int:
    from app.core.config import get_settings

    s = get_settings()
    return {
        IntervalClass.FAST: s.interval_fast_seconds,
        IntervalClass.NORMAL: s.interval_normal_seconds,
        IntervalClass.SLOW: s.interval_slow_seconds,
        IntervalClass.VERY_SLOW: s.interval_very_slow_seconds,
    }[cls]


def _due(state: dict[str, Any], cls: IntervalClass, ts: datetime) -> bool:
    """Slow-class metrics (storage, capacity, configuration-like levels) are written at their
    class interval even when the snapshot is collected more often."""
    if cls not in (IntervalClass.SLOW, IntervalClass.VERY_SLOW):
        return True
    last = state.get(f"last_{cls.value}")
    return last is None or ts.timestamp() - float(last) >= interval_seconds(cls) - 1


def normalize_snapshot(
    server: Any, snap: MetricSnapshot, derived: dict[str, float], state: dict[str, Any] | None = None
) -> list[Sample]:
    """Gauges + derived rates + raw counters + labeled values of one collection.

    ``state`` (the server's collector state for the time-series writer) is updated in place
    with the time slow-class metrics were last written.
    """
    state = state if state is not None else {}
    ts = snap.collected_at if snap.collected_at.tzinfo else snap.collected_at.replace(tzinfo=UTC)
    labels = base_labels(server)
    due = {cls: _due(state, cls, ts) for cls in IntervalClass}
    out: list[Sample] = []

    def add(name: str, extra: dict[str, str], value: Any) -> None:
        if value is None:
            return
        try:
            out.append(Sample.make(name, {**labels, **extra}, float(value), ts))
        except (LabelPolicyError, TypeError, ValueError) as exc:
            METRICS_DROPPED.labels("label_policy").inc()
            log.debug("sample dropped", extra={"metric": name, "error": str(exc)})

    for key, value in {**snap.gauges, **derived}.items():
        d = BY_KEY.get(key)
        if d is not None and not due[d.interval]:
            continue
        add(name_for(key), {}, value)
    for key, value in snap.counters.items():
        add(counter_name(key), {}, value)
    for key, lbl, value in snap.labeled:
        mapped = {LABEL_MAP[k]: str(v)[:128] for k, v in lbl.items() if k in LABEL_MAP}
        if len(mapped) != len(lbl):
            METRICS_DROPPED.labels("label_policy").inc()
            continue
        add(name_for(key), mapped, value)
    for cls in (IntervalClass.SLOW, IntervalClass.VERY_SLOW):
        if due[cls]:
            state[f"last_{cls.value}"] = ts.timestamp()
    return out


def normalize_query_text(text: str) -> str:
    """Literal-free, whitespace-normalized statement used for the fingerprint."""
    return _WS.sub(" ", mask_sql(text or "")).strip()


def fingerprint(engine: str, text: str) -> str:
    """Stable 16-hex-digit id of a normalized statement (independent of engine query ids)."""
    norm = normalize_query_text(text).lower()
    return hashlib.sha256(f"{engine}\x00{norm}".encode()).hexdigest()[:16]


@dataclass
class QueryFingerprintRow:
    fingerprint: str
    database: str | None
    username: str | None
    normalized_text: str
    engine_query_id: str | None


def normalize_query_stats(
    server: Any,
    stats: list[QueryStat],
    ts: datetime,
    *,
    top_n: int,
    known: set[str] | None = None,
    max_fingerprints: int | None = None,
) -> tuple[list[Sample], list[QueryFingerprintRow]]:
    """Query telemetry samples (keyed by fingerprint, never by SQL text) and the fingerprint
    metadata rows for PostgreSQL.

    Cardinality guards: only the ``top_n`` statements by total time are kept per collection,
    and a server never gets more than ``max_fingerprints`` distinct fingerprints (``known`` are
    the ones it already has); statements beyond the cap are counted as dropped instead of
    creating new series."""
    known = set(known or ())
    labels = base_labels(server)
    # The same normalized statement can be reported several times (per user, per plan, or
    # texts that differ only in literals): merge them so each (database, fingerprint) series
    # gets exactly one sample per collection.
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for q in stats:
        fp = fingerprint(server.engine, q.query_text)
        key = ((q.database or "")[:128], fp)
        m = merged.get(key)
        if m is None:
            merged[key] = {
                "q": q,
                "_plan_ms": q.total_ms or 0,
                **{d.key: getattr(q, d.key, None) for d in QUERY_METRICS},
            }
            continue
        if q.plan_id is not None and (q.total_ms or 0) > m["_plan_ms"]:
            m["plan_id"], m["_plan_ms"] = q.plan_id, q.total_ms or 0  # the dominant plan of the merged statement
        for d in QUERY_METRICS:
            v = getattr(q, d.key, None)
            if v is None or d.key in ("mean_ms", "plan_id"):
                continue
            if d.key in ("max_ms", "plans"):
                m[d.key] = max(m[d.key] or 0, v)
            else:
                m[d.key] = (m[d.key] or 0) + v
    for m in merged.values():
        if m.get("calls"):
            m["mean_ms"] = (m.get("total_ms") or 0) / m["calls"]
    ranked = sorted(merged.items(), key=lambda kv: kv[1].get("total_ms") or 0, reverse=True)[:top_n]
    samples: list[Sample] = []
    rows: list[QueryFingerprintRow] = []
    for (db, fp), m in ranked:
        if fp not in known:
            if max_fingerprints is not None and len(known) >= max_fingerprints:
                METRICS_DROPPED.labels("query_cardinality").inc()
                continue
            known.add(fp)
        q = m["q"]
        extra = {"database": db, "fingerprint": fp}
        for d in QUERY_METRICS:
            v = m.get(d.key)
            if v is None:
                continue
            try:
                samples.append(Sample.make(d.name, {**labels, **extra}, float(v), ts))
            except (LabelPolicyError, ValueError):
                METRICS_DROPPED.labels("label_policy").inc()
        rows.append(
            QueryFingerprintRow(
                fingerprint=fp,
                database=q.database,
                username=q.username,
                normalized_text=normalize_query_text(q.query_text)[:20000],
                engine_query_id=str(q.query_id)[:64] if q.query_id else None,
            )
        )
    return samples, rows
