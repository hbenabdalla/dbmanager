"""Simulation (demo fleet) administration — Platform Administrator only by default.

Every endpoint checks a ``simulation.*`` permission at platform scope; the UI hiding the page is
only a convenience. Denials are audited by the global PermissionDenied handler.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentPrincipal
from app.core.errors import ConfirmationRequired
from app.simulation import service

router = APIRouter(prefix="/simulation", tags=["simulation"])

REMOVE_WORD = "REMOVE"
REGENERATE_WORD = "REGENERATE"


class GenerateRequest(BaseModel):
    profile: str = Field("standard", max_length=32, description="small | standard | large (or a registered profile)")
    seed: int | None = Field(None, ge=0, le=2_147_483_647, description="same profile + seed => same estate")


class RegenerateRequest(GenerateRequest):
    confirm: str | None = Field(None, description=f"type {REGENERATE_WORD} to confirm")


@router.get("/status", summary="Simulated fleet status, counts and the active run")
async def simulation_status(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("simulation.view")
    return await service.status(db)


@router.get("/profiles", summary="Available simulation profiles")
async def simulation_profiles(p: CurrentPrincipal) -> list[dict[str, object]]:
    p.authz.require("simulation.view")
    return service.profiles()


@router.get("/cleanup-preview", summary="What removing the simulation would delete")
async def cleanup_preview(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("simulation.delete")
    return await service.cleanup_report(db)


@router.post("/generate", status_code=202, summary="Queue generation of a simulated fleet")
async def generate(body: GenerateRequest, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("simulation.generate")
    run = await service.request_generation(db, p, profile=body.profile, seed=body.seed)
    return {"run": service.run_dict(run), "message": "Generation queued"}


@router.post("/regenerate", status_code=202, summary="Replace the simulated fleet with a new one")
async def regenerate(body: RegenerateRequest, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("simulation.generate")
    p.authz.require("simulation.delete")
    if body.confirm != REGENERATE_WORD:
        raise ConfirmationRequired(
            f"Type {REGENERATE_WORD} to replace all simulated data",
            details={"expected": REGENERATE_WORD, "risk": "DESTRUCTIVE (simulated data only)"},
        )
    run = await service.request_generation(db, p, profile=body.profile, seed=body.seed, operation="regenerate")
    return {"run": service.run_dict(run), "message": "Regeneration queued"}


@router.delete("", summary="Remove all simulated data (real resources are not affected)")
async def remove(
    db: DB, p: CurrentPrincipal, confirm: str | None = Query(None, description=f"type {REMOVE_WORD} to confirm")
) -> dict[str, Any]:
    p.authz.require("simulation.delete")
    if confirm != REMOVE_WORD:
        raise ConfirmationRequired(
            f"Type {REMOVE_WORD} to delete all simulated servers, metrics, query statistics, findings and "
            "recommendations. Real database resources and platform configuration are not affected.",
            details={"expected": REMOVE_WORD, "risk": "DESTRUCTIVE (simulated data only)"},
        )
    result = await service.remove(db, p)
    return {"message": "Simulated data removed", **result}
