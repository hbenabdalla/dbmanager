"""Environments, sites, clusters, applications."""

import uuid
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DB, CurrentPrincipal
from app.core.errors import Conflict, NotFound
from app.models import Application, Cluster, Environment, Organization, Server, Site
from app.schemas.common import Message
from app.schemas.inventory import (
    ApplicationIn,
    ApplicationOut,
    ClusterIn,
    ClusterOut,
    EnvironmentIn,
    EnvironmentOut,
    SiteIn,
    SiteOut,
)
from app.services import audit

router = APIRouter(tags=["inventory"])

_KINDS: dict[str, tuple[Any, Any, Any, str]] = {
    "environments": (Environment, EnvironmentIn, EnvironmentOut, "environment_id"),
    "sites": (Site, SiteIn, SiteOut, "site_id"),
    "clusters": (Cluster, ClusterIn, ClusterOut, "cluster_id"),
    "applications": (Application, ApplicationIn, ApplicationOut, "application_id"),
}


async def _org(db: DB) -> Organization:
    return (await db.execute(select(Organization).order_by(Organization.created_at).limit(1))).scalar_one()


def _make(kind: str) -> None:
    model, schema_in, schema_out, fk = _KINDS[kind]
    singular = kind[:-1]

    async def list_items(db: DB, p: CurrentPrincipal) -> list[Any]:
        order = model.sort_order if model is Environment else model.name
        rows = (await db.execute(select(model).order_by(order))).scalars().all()
        counts = dict((await db.execute(select(getattr(Server, fk), func.count()).group_by(getattr(Server, fk)))).all())
        out = []
        for r in rows:
            item = schema_out.model_validate(r).model_dump()
            item["server_count"] = counts.get(r.id, 0) if p.authz.has_anywhere("server.view") else None
            out.append(item)
        return out

    async def create_item(body: schema_in, db: DB, p: CurrentPrincipal) -> Any:  # type: ignore[valid-type]
        p.authz.require("inventory.manage")
        if (await db.execute(select(model).where(model.name == body.name))).scalar_one_or_none():
            raise Conflict(f"A {singular} with this name already exists")
        obj = model(organization_id=(await _org(db)).id, **body.model_dump())
        db.add(obj)
        await db.flush()
        await audit.record(
            f"{singular}.create", actor=p, target_type=singular, target_id=str(obj.id), object_name=obj.name
        )
        return schema_out.model_validate(obj)

    async def update_item(item_id: uuid.UUID, body: schema_in, db: DB, p: CurrentPrincipal) -> Any:  # type: ignore[valid-type]
        p.authz.require("inventory.manage")
        obj = await db.get(model, item_id)
        if obj is None:
            raise NotFound(f"{singular} not found")
        for k, v in body.model_dump().items():
            setattr(obj, k, v)
        await audit.record(
            f"{singular}.update", actor=p, target_type=singular, target_id=str(obj.id), object_name=obj.name
        )
        return schema_out.model_validate(obj)

    async def delete_item(item_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
        p.authz.require("inventory.manage")
        obj = await db.get(model, item_id)
        if obj is None:
            raise NotFound(f"{singular} not found")
        in_use = (
            await db.execute(select(func.count()).select_from(Server).where(getattr(Server, fk) == obj.id))
        ).scalar_one()
        if in_use:
            raise Conflict(f"{in_use} server(s) still reference this {singular}")
        await db.delete(obj)
        await audit.record(
            f"{singular}.delete", actor=p, target_type=singular, target_id=str(item_id), object_name=obj.name
        )
        return Message(message=f"{singular.capitalize()} deleted")

    router.add_api_route(f"/{kind}", list_items, methods=["GET"], name=f"list_{kind}", summary=f"List {kind}")
    router.add_api_route(
        f"/{kind}",
        create_item,
        methods=["POST"],
        status_code=201,
        response_model=schema_out,
        name=f"create_{singular}",
        summary=f"Create {singular}",
    )
    router.add_api_route(
        f"/{kind}/{{item_id}}",
        update_item,
        methods=["PUT"],
        response_model=schema_out,
        name=f"update_{singular}",
        summary=f"Update {singular}",
    )
    router.add_api_route(
        f"/{kind}/{{item_id}}",
        delete_item,
        methods=["DELETE"],
        response_model=Message,
        name=f"delete_{singular}",
        summary=f"Delete {singular}",
    )


for _k in _KINDS:
    _make(_k)
