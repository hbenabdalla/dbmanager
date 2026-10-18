"""Runtime-editable platform settings (stored in app_settings) with typed defaults."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailed
from app.models import AppSetting

EDITABLE: dict[str, tuple[type, Any, str]] = {
    "ui.banner_text": (str, "", "Banner shown to all users (e.g. maintenance notice)"),
    "workspace.allow_writes_in_production": (
        bool,
        True,
        "Allow data-modifying workspace statements on production servers",
    ),
    "workspace.allow_explain_analyze_in_production": (bool, True, "Allow EXPLAIN ANALYZE on production servers"),
    "collection.default_interval_s": (int, 60, "Default metric collection interval for new servers"),
    "security.dormant_account_days": (int, 90, "Days without login before an account is considered dormant"),
    "monitoring.retention_days": (
        int,
        90,
        "Monitoring telemetry retention policy in days (enforced by the time-series database; "
        "a change takes effect after restarting it with DBM_TIMESERIES_RETENTION)",
    ),
    "ai_insights.enabled": (bool, False, "Optional AI insights layer (not available in this release)"),
}


async def get_all(db: AsyncSession) -> dict[str, Any]:
    stored = {s.key: s.value for s in (await db.execute(select(AppSetting))).scalars()}
    return {k: stored.get(k, default) for k, (_t, default, _d) in EDITABLE.items()}


async def get(db: AsyncSession, key: str) -> Any:
    row = await db.get(AppSetting, key)
    return row.value if row else EDITABLE[key][1]


async def put(db: AsyncSession, values: dict[str, Any], actor: str) -> dict[str, Any]:
    for k, v in values.items():
        if k not in EDITABLE:
            raise ValidationFailed(f"unknown setting {k!r}")
        typ = EDITABLE[k][0]
        if (
            typ is bool
            and not isinstance(v, bool)
            or typ is int
            and (not isinstance(v, int) or isinstance(v, bool))
            or typ is str
            and not isinstance(v, str)
        ):
            raise ValidationFailed(f"setting {k!r} must be {typ.__name__}")
        if k == "monitoring.retention_days" and not 1 <= v <= 3650:
            raise ValidationFailed("retention must be between 1 and 3650 days")
        if k == "ai_insights.enabled" and v:
            raise ValidationFailed("The AI insights layer is not available in this release")
        row = await db.get(AppSetting, k)
        if row is None:
            db.add(AppSetting(key=k, value=v, updated_by=actor))
        else:
            row.value, row.updated_by = v, actor
    await db.flush()
    return await get_all(db)
