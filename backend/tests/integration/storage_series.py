"""Capacity-forecast series derived from real storage data (shared by the engine tests)."""

from __future__ import annotations


def size_series(engine: str, databases: list, tablespaces: list) -> dict[str, set[str]]:  # type: ignore[type-arg]
    """Capacity-forecast series (0.13) the inventory discovery would write from real storage
    data: metric name → the database / tablespace labels it carries."""
    import types
    from datetime import UTC, datetime

    from app.services.collection import size_samples

    server = types.SimpleNamespace(id="00000000-0000-0000-0000-000000000001", engine=engine, data_source="real")
    out: dict[str, set[str]] = {}
    for smp in size_samples(server, databases, tablespaces, datetime.now(UTC)):  # type: ignore[arg-type]
        lbl = smp.label_dict
        out.setdefault(smp.name, set()).add(lbl.get("database") or lbl.get("tablespace") or "")
    return out
