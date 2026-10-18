"""Forwarding of audit events to a SIEM (sprint 0.11.4).

Destinations:

* **syslog** — RFC 5424 messages over UDP (RFC 5426), TCP (octet counting, RFC 6587) or TLS
  (RFC 5425, server certificate verified against the system trust store or a configured CA).
  Each audit event is one message: ``<PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [SD] MSG``
  with the event id, action, result, category, user, source IP and server as structured data and
  the full event as JSON in MSG.
* **webhook** — a JSON POST of a batch of events (``{"source": "dbmanager", "events": [...]}``),
  optionally HMAC-SHA256 signed (``X-DBM-Signature``), with the same SSRF guard as notification
  webhooks.

Delivery is at-least-once: the ``siem_export`` job reads events after each destination's
``cursor_id`` (oldest first, settled for a few seconds) and advances the cursor only after a
batch was accepted; a failed destination keeps its position and is retried on the next run.
Audit events never contain passwords or secrets (see app.services.audit); messages are passed
through :mod:`app.core.redact` once more before they leave the platform.
"""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redact
from app.core.config import get_settings
from app.core.crypto import EncryptedBlob, get_keyring
from app.models import AuditEvent, SiemDestination

KINDS = ("syslog", "webhook")
TRANSPORTS = ("udp", "tcp", "tls")
#: RFC 5424 facilities by name; default "audit" (13, log audit)
FACILITIES = {
    "kern": 0,
    "user": 1,
    "auth": 4,
    "authpriv": 10,
    "audit": 13,
    "local0": 16,
    "local1": 17,
    "local2": 18,
    "local3": 19,
    "local4": 20,
    "local5": 21,
    "local6": 22,
    "local7": 23,
}
#: default SD-ID: 32473 is the example private enterprise number of RFC 5612; organisations with
#: their own PEN can set ``sd_id`` (e.g. ``audit@<pen>``)
DEFAULT_SD_ID = "dbm@32473"
BATCH = 500
SETTLE_S = 5
UDP_MAX = 8192


class ForwardError(Exception):
    pass


def _aad(dest_id: object) -> str:
    return f"siem-destination:{dest_id}"


def seal_secret(dest_id: object, value: str | None) -> dict[str, Any] | None:
    return get_keyring().encrypt(value, _aad(dest_id)).to_dict() if value else None


def open_secret(dest: SiemDestination) -> str | None:
    if not dest.secret:
        return None
    return get_keyring().decrypt(EncryptedBlob.from_dict(dest.secret), _aad(dest.id))


def event_dict(e: AuditEvent) -> dict[str, Any]:
    return redact.data(
        {
            "id": e.id,
            "ts": e.ts.isoformat(),
            "action": e.action,
            "category": e.category,
            "result": e.result,
            "username": e.username,
            "auth_source": e.auth_source,
            "source_ip": e.source_ip,
            "risk_class": e.risk_class,
            "target_type": e.target_type,
            "target_id": e.target_id,
            "server_id": str(e.server_id) if e.server_id else None,
            "server_name": e.server_name,
            "database": e.database,
            "object_name": e.object_name,
            "failure_reason": e.failure_reason,
            "change_reason": e.change_reason,
            "request_id": e.request_id,
            "details": e.details,
        }
    )


# ---- RFC 5424 -------------------------------------------------------------------------------------
def _sd_value(v: object) -> str:
    """PARAM-VALUE escaping (RFC 5424 §6.3.3): backslash, double quote and closing bracket."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")


def _token(v: str | None, max_len: int) -> str:
    """HOSTNAME / APP-NAME / MSGID: printable US-ASCII without spaces, ``-`` when empty."""
    s = "".join(ch for ch in (v or "") if 33 <= ord(ch) <= 126)[:max_len]
    return s or "-"


def severity(event: dict[str, Any]) -> int:
    if event.get("result") in ("failure", "denied"):
        return 4  # warning
    if event.get("category") in ("security", "auth") or event.get("risk_class") == "DESTRUCTIVE":
        return 5  # notice
    return 6  # informational


def rfc5424(event: dict[str, Any], config: dict[str, Any], hostname: str | None = None) -> bytes:
    facility = config.get("facility", "audit")
    fac = FACILITIES.get(str(facility), facility if isinstance(facility, int) else 13)
    pri = int(fac) * 8 + severity(event)
    ts = datetime.fromisoformat(event["ts"]).astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    sd_id = config.get("sd_id") or DEFAULT_SD_ID
    params = {
        "id": event["id"],
        "action": event["action"],
        "result": event["result"],
        "category": event["category"],
        "user": event.get("username"),
        "src": event.get("source_ip"),
        "server": event.get("server_name"),
        "db": event.get("database"),
        "object": event.get("object_name"),
        "risk": event.get("risk_class"),
    }
    sd = (
        "["
        + _sd_value(sd_id).replace(" ", "")
        + "".join(f' {k}="{_sd_value(v)}"' for k, v in params.items() if v not in (None, ""))
        + "]"
    )
    header = " ".join(
        [
            f"<{pri}>1",
            ts,
            _token(hostname or config.get("hostname") or socket.gethostname(), 255),
            _token(config.get("app_name") or "dbmanager", 48),
            "-",
            _token(event["action"], 32),
            sd,
        ]
    )
    msg = json.dumps(event, default=str, separators=(",", ":"), ensure_ascii=False)
    return (header + " ").encode() + b"\xef\xbb\xbf" + msg.encode()


# ---- transports -----------------------------------------------------------------------------------
def _tls_context(config: dict[str, Any]) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if config.get("ca_pem"):
        ctx.load_verify_locations(cadata=str(config["ca_pem"]))
    return ctx


def _send_syslog_sync(config: dict[str, Any], messages: list[bytes]) -> None:
    host, port = str(config["host"]), int(config.get("port") or (6514 if config.get("transport") == "tls" else 514))
    transport = config.get("transport", "udp")
    timeout = float(config.get("timeout", 10))
    if transport == "udp":
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            for m in messages:
                s.sendto(m[:UDP_MAX], (host, port))
        return
    raw = socket.create_connection((host, port), timeout=timeout)
    try:
        conn = _tls_context(config).wrap_socket(raw, server_hostname=host) if transport == "tls" else raw
        with conn:
            # octet counting framing (RFC 6587 §3.4.1 / RFC 5425 §4.3)
            conn.sendall(b"".join(str(len(m)).encode() + b" " + m for m in messages))
    finally:
        raw.close()


async def _send_webhook(dest: SiemDestination, events: list[dict[str, Any]]) -> None:
    from app.services import notify

    url = str(dest.config.get("url") or "")
    try:
        await notify.check_target(url)
    except notify.DeliveryError as exc:
        raise ForwardError(str(exc)) from exc
    body = json.dumps({"source": "dbmanager", "events": events}, default=str).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "dbmanager-siem"}
    for k, v in (dest.config.get("headers") or {}).items():
        if k.lower() not in ("host", "content-length"):
            headers[str(k)] = str(v)
    secret = open_secret(dest)
    if secret:
        headers[notify.SIGNATURE_HEADER] = notify.sign(secret, body)
    try:
        async with httpx.AsyncClient(timeout=get_settings().webhook_timeout_seconds, follow_redirects=False) as c:
            r = await c.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise ForwardError(f"{type(exc).__name__}: {exc}") from exc
    if r.status_code >= 300:
        raise ForwardError(f"receiver answered HTTP {r.status_code}")


async def send(dest: SiemDestination, events: list[dict[str, Any]]) -> None:
    if dest.kind == "syslog":
        msgs = [rfc5424(e, dest.config) for e in events]
        try:
            await asyncio.to_thread(_send_syslog_sync, dest.config, msgs)
        except (OSError, ssl.SSLError) as exc:
            raise ForwardError(f"{type(exc).__name__}: {exc}") from exc
    elif dest.kind == "webhook":
        await _send_webhook(dest, events)
    else:
        raise ForwardError(f"unknown destination kind {dest.kind!r}")


# ---- export job -----------------------------------------------------------------------------------
async def _pending(db: AsyncSession, dest: SiemDestination, now: datetime, limit: int) -> list[AuditEvent]:
    q = select(AuditEvent).where(AuditEvent.id > dest.cursor_id, AuditEvent.ts <= now - timedelta(seconds=SETTLE_S))
    if dest.categories:
        q = q.where(AuditEvent.category.in_(dest.categories))
    return list((await db.execute(q.order_by(AuditEvent.id).limit(limit))).scalars())


async def export(db: AsyncSession, now: datetime | None = None, max_batches: int = 20) -> dict[str, Any]:
    """Global job ``siem_export``: forward new audit events to every enabled destination."""
    now = now or datetime.now(UTC)
    dests = list(
        (
            await db.execute(
                select(SiemDestination).where(SiemDestination.enabled.is_(True)).with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    out: dict[str, Any] = {"destinations": len(dests), "forwarded": 0, "failed": 0}
    for d in dests:
        for _ in range(max_batches):
            events = await _pending(db, d, now, BATCH)
            if not events:
                break
            try:
                await send(d, [event_dict(e) for e in events])
            except ForwardError as exc:
                d.last_error, d.last_error_at = str(exc)[:2000], now
                out["failed"] += 1
                break
            d.cursor_id = events[-1].id
            d.exported_count += len(events)
            d.last_success_at, d.last_error = now, None
            out["forwarded"] += len(events)
            if len(events) < BATCH:
                break
    await db.flush()
    return out


async def lag(db: AsyncSession, dest: SiemDestination) -> int:
    """Audit events (matching the destination's categories) not forwarded yet."""
    q = select(func.count()).select_from(AuditEvent).where(AuditEvent.id > dest.cursor_id)
    if dest.categories:
        q = q.where(AuditEvent.category.in_(dest.categories))
    return int((await db.execute(q)).scalar_one())


async def head_id(db: AsyncSession) -> int:
    return int((await db.execute(select(func.coalesce(func.max(AuditEvent.id), 0)))).scalar_one())
