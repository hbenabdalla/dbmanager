"""SIEM forwarding destinations: audit events to syslog (RFC 5424) or a webhook."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal, rate_limit
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import SiemDestination
from app.schemas.common import Message
from app.services import audit, siem

router = APIRouter(prefix="/platform/siem-destinations", tags=["siem"])


class DestinationIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    kind: Literal["syslog", "webhook"]
    enabled: bool = True
    #: syslog: host, port, transport udp|tcp|tls, facility, app_name, sd_id, ca_pem; webhook: url, headers
    config: dict[str, Any] = Field(default_factory=dict)
    categories: list[str] = Field(default_factory=list, max_length=20)
    #: webhook HMAC signing secret (write-only; omit to keep, "" to clear)
    signing_secret: str | None = Field(None, max_length=512)
    #: where forwarding starts for a new destination: only new events, or the whole audit log
    start_from: Literal["now", "beginning"] = "now"

    @model_validator(mode="after")
    def _check(self) -> DestinationIn:
        unknown = sorted(set(self.categories) - set(audit.CATEGORIES))
        if unknown:
            raise ValueError(f"unknown audit categories {', '.join(unknown)} (known: {', '.join(audit.CATEGORIES)})")
        cfg = self.config
        bad = [k for k in cfg if any(s in k.lower() for s in ("password", "secret", "token"))]
        if bad:
            raise ValueError(f"put secrets in signing_secret, not in config ({', '.join(bad)})")
        if self.kind == "syslog":
            if not cfg.get("host"):
                raise ValueError("syslog destinations need a host")
            if cfg.get("transport", "udp") not in siem.TRANSPORTS:
                raise ValueError("transport must be udp, tcp or tls")
            fac = cfg.get("facility", "audit")
            if not (fac in siem.FACILITIES or (isinstance(fac, int) and 0 <= fac <= 23)):
                raise ValueError(f"unknown syslog facility {fac!r}")
            try:
                port = int(cfg.get("port") or 514)
            except (TypeError, ValueError) as exc:
                raise ValueError("port must be a number") from exc
            if not 1 <= port <= 65535:
                raise ValueError("port must be 1–65535")
        else:
            from app.services import notify

            notify.validate_webhook_url(str(cfg.get("url") or ""))
            for k in cfg.get("headers") or {}:
                if k.lower() in ("authorization", "proxy-authorization", "cookie") or any(
                    w in k.lower() for w in ("password", "secret", "token", "api-key", "apikey")
                ):
                    raise ValueError(f"header {k!r} would be stored unencrypted: use signing_secret instead")
        return self


def _out(d: SiemDestination, lag: int | None = None) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "name": d.name,
        "kind": d.kind,
        "enabled": d.enabled,
        "config": d.config,
        "categories": d.categories,
        "has_signing_secret": bool(d.secret),
        "cursor_id": d.cursor_id,
        "exported_count": d.exported_count,
        "pending": lag,
        "last_success_at": d.last_success_at,
        "last_error": d.last_error,
        "last_error_at": d.last_error_at,
        "created_by": d.created_by,
        "created_at": d.created_at,
    }


def _stored_config(body: DestinationIn) -> dict[str, Any]:
    keys = ("host", "port", "transport", "facility", "app_name", "sd_id", "ca_pem", "hostname", "timeout")
    if body.kind == "syslog":
        return {k: body.config[k] for k in keys if k in body.config}
    return {
        "url": body.config["url"],
        "headers": {str(k): str(v) for k, v in (body.config.get("headers") or {}).items()},
    }


@router.get("/categories", summary="Audit event categories a destination can filter on")
async def categories(p: CurrentPrincipal) -> list[str]:
    p.authz.require("audit.forward")
    return list(audit.CATEGORIES)


@router.get("", summary="Audit forwarding destinations with their position and backlog")
async def list_destinations(db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    p.authz.require("audit.forward")
    rows = list((await db.execute(select(SiemDestination).order_by(SiemDestination.name))).scalars())
    return [_out(d, await siem.lag(db, d)) for d in rows]


@router.post("", status_code=201, summary="Add an audit forwarding destination")
async def create_destination(body: DestinationIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("audit.forward")
    if (await db.execute(select(SiemDestination.id).where(SiemDestination.name == body.name))).first():
        raise Conflict("a destination with this name already exists")
    did = uuid.uuid4()
    d = SiemDestination(
        id=did,
        name=body.name,
        kind=body.kind,
        enabled=body.enabled,
        config=_stored_config(body),
        categories=body.categories,
        secret=siem.seal_secret(did, body.signing_secret) if body.kind == "webhook" else None,
        cursor_id=0 if body.start_from == "beginning" else await siem.head_id(db),
        created_by=p.username,
    )
    db.add(d)
    await db.flush()
    await audit.record(
        "siem_destination.create",
        actor=p,
        category="security",
        object_name=d.name,
        details={
            "kind": d.kind,
            "config": {k: v for k, v in d.config.items() if k != "ca_pem"},
            "start_from": body.start_from,
        },
    )
    return _out(d, await siem.lag(db, d))


@router.put("/{dest_id}", summary="Change an audit forwarding destination (its position is kept)")
async def update_destination(dest_id: uuid.UUID, body: DestinationIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("audit.forward")
    d = await db.get(SiemDestination, dest_id)
    if d is None:
        raise NotFound("Destination not found")
    if body.kind != d.kind:
        raise ValidationFailed("the destination kind cannot be changed; add a new destination")
    d.name, d.enabled, d.config, d.categories = body.name, body.enabled, _stored_config(body), body.categories
    if body.kind == "webhook" and body.signing_secret is not None:
        d.secret = siem.seal_secret(d.id, body.signing_secret)
    await audit.record(
        "siem_destination.update",
        actor=p,
        category="security",
        object_name=d.name,
        details={"enabled": d.enabled, "categories": d.categories, "secret_changed": body.signing_secret is not None},
    )
    return _out(d, await siem.lag(db, d))


@router.delete("/{dest_id}", summary="Remove an audit forwarding destination")
async def delete_destination(dest_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("audit.forward")
    d = await db.get(SiemDestination, dest_id)
    if d is None:
        raise NotFound("Destination not found")
    name = d.name
    await db.delete(d)
    await audit.record("siem_destination.delete", actor=p, category="security", object_name=name)
    return Message(message=f"Destination {name} removed")


@router.post("/{dest_id}/test", summary="Send one test event", dependencies=[rate_limit("expensive")])
async def test_destination(dest_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("audit.forward")
    d = await db.get(SiemDestination, dest_id)
    if d is None:
        raise NotFound("Destination not found")
    event = {
        "id": 0,
        "ts": datetime.now(UTC).isoformat(),
        "action": "siem.test",
        "category": "security",
        "result": "success",
        "username": p.username,
        "details": {"message": "Test event from DB Manager"},
    }
    try:
        await siem.send(d, [event])
        ok, error = True, None
    except siem.ForwardError as exc:
        ok, error = False, str(exc)
    await audit.record(
        "siem_destination.test",
        actor=p,
        category="security",
        object_name=d.name,
        result="success" if ok else "failure",
        failure_reason=error,
    )
    return {"ok": ok, "error": error}
