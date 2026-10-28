"""Product information: version, build, schema revision, supported engines and links.

Nothing here exposes host names, file paths or environment contents: build metadata comes
only from the explicit ``DBM_BUILD_VERSION`` / ``DBM_GIT_COMMIT`` / ``DBM_BUILD_DATE`` values
baked into the image at build time.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app import DOCUMENTATION_URL, __license__, __version__
from app.api.deps import DB, CurrentPrincipal
from app.core.config import get_settings

router = APIRouter(tags=["about"])

#: customer-facing product name (KBM corporate rebrand, 0.16)
PRODUCT = "KBM DBManager"
#: stable machine identifier of the public /version endpoint (load-balancer checks and upgrade
#: scripts match it): unchanged by the rebrand
PRODUCT_ID = "DBManager"
#: the roadmap milestone this build belongs to (kept in step with ROADMAP.md)
CURRENT_MILESTONE = {"version": "v0.16", "name": "KBM Corporate Rebranding & Visual Identity"}
_SAFE = re.compile(r"[^0-9A-Za-z.+_:\-]")


def _clean(value: str, limit: int = 64) -> str | None:
    """Build values are user-supplied at build time: keep them short and printable."""
    v = _SAFE.sub("", value or "")[:limit]
    return v or None


_HIDDEN = {"off", "none", "false", "-"}


def links() -> dict[str, str | None]:
    s = get_settings()
    github = s.github_url.strip() or None
    if github and github.lower() in _HIDDEN:
        github = None
    roadmap = s.roadmap_url.strip() or (f"{github.rstrip('/')}/blob/main/ROADMAP.md" if github else None)
    if roadmap and roadmap.lower() in _HIDDEN:
        roadmap = None
    docs: str | None = s.documentation_url.strip() or DOCUMENTATION_URL
    if docs.lower() in _HIDDEN:
        docs = None
    return {"documentation": docs, "github": github, "roadmap": roadmap}


def version_info(schema_version: str | None = None) -> dict[str, Any]:
    s = get_settings()
    return {
        "application": __version__,
        "backend": __version__,
        "build": _clean(s.build_version),
        "git_commit": _clean(s.git_commit, 40),
        "build_date": _clean(s.build_date, 32),
        "schema": schema_version,
    }


@router.get("/version", summary="Product version (no authentication required)")
async def version() -> dict[str, Any]:
    return {"product": PRODUCT_ID, "version": __version__}


@router.get("/about", summary="Product, version, engines, status and links")
async def about(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    from app.adapters.registry import supported_engines
    from app.api.ops import timeseries_component
    from app.api.v1.platform import _validation_for, real_engine_validation
    from app.jobs import runner as jobs

    try:
        schema = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        db_status = "ok"
    except Exception:  # noqa: BLE001 - reported as a status, never as details
        schema, db_status = None, "unavailable"
    ts = await timeseries_component()
    r = jobs.runner
    validation = real_engine_validation()
    engines = []
    for name, cls in supported_engines().items():
        if name == "demo":
            continue
        val = _validation_for(name, validation) if cls.implemented else None
        engines.append(
            {
                "engine": name,
                "display_name": cls.display_name,
                "support_level": cls.support_level if cls.implemented else "planned",
                "validation": val["status"] if val else None,
                "validated_version": val["version"] if val else None,
            }
        )
    s = get_settings()
    from app.services import instances

    try:
        summary = (await instances.list_instances(db))["summary"]
        # every signed-in user reads this page: counts only, no host names or process ids
        # (the per-process view is Platform -> Collectors, permission collectors.view)
        deployment = {
            "api": summary["api"],
            "workers": summary["workers"],
            "stale": summary["stale"],
            "has_leader": summary["leader"] is not None,
            "versions": summary["versions"],
            "mixed_versions": summary["mixed_versions"],
        }
    except Exception:  # noqa: BLE001 - informational
        deployment = None
    if r and r.started_at:
        background = "running"
    elif deployment and deployment["workers"]:
        background = "running_elsewhere"  # another replica runs the collectors (API-only pod)
    else:
        background = "stopped" if s.runs_worker else "no_worker"
    return {
        "product": PRODUCT,
        "description": "Unified administration, monitoring, performance, security and optimization "
        "for heterogeneous relational databases",
        "version": version_info(schema),
        "milestone": CURRENT_MILESTONE,
        "license": s.license.strip() or __license__,
        "links": links(),
        "engines": engines,
        "status": {
            "api": "ok",
            "database": db_status,
            "timeseries": ts.get("status"),
            "background_jobs": background,
        },
        "deployment": deployment,
    }
