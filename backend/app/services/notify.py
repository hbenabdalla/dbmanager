"""Notification channels: webhook (signed JSON POST) and email (SMTP).

Channel secrets (webhook signing secret, SMTP password) are envelope-encrypted with the
platform keyring and only decrypted in memory while a notification is sent; they are never
returned by the API. Webhook targets on private, loopback, link-local or otherwise
non-public addresses are refused unless ``DBM_WEBHOOK_ALLOW_PRIVATE=true`` (SSRF guard).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import smtplib
import socket
import ssl
import uuid
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings
from app.core.crypto import EncryptedBlob, get_keyring
from app.core.errors import ValidationFailed
from app.models import NotificationChannel

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
KINDS = ("webhook", "email")
#: webhook payload formats: DBManager JSON, Slack incoming webhook, Microsoft Teams workflow
#: (Adaptive Card message, as accepted by Teams "Post to a channel when a webhook request is received")
PRESETS = ("generic", "slack", "teams")
SMTP_SECURITY = ("starttls", "tls", "none")
SIGNATURE_HEADER = "X-DBM-Signature"


class DeliveryError(Exception):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


# ---- secrets ----------------------------------------------------------------------------------
def _aad(channel_id: uuid.UUID | str) -> str:
    return f"notification-channel:{channel_id}"


def seal_secret(channel_id: uuid.UUID, values: dict[str, Any]) -> dict[str, Any] | None:
    values = {k: v for k, v in values.items() if v}
    if not values:
        return None
    return get_keyring().encrypt(json.dumps(values), _aad(channel_id)).to_dict()


def open_secret(channel: NotificationChannel) -> dict[str, Any]:
    if not channel.secret:
        return {}
    return json.loads(get_keyring().decrypt(EncryptedBlob.from_dict(channel.secret), _aad(channel.id)))


def webhook_target(channel: NotificationChannel) -> tuple[str, dict[str, str]]:
    """URL and extra headers of a webhook channel. Both are stored encrypted (a Slack or Teams
    incoming-webhook URL *is* the credential); values still in ``config`` come from installations
    before 0.14 and are moved into the secret at startup (``migrate_plaintext_webhooks``)."""
    secret = open_secret(channel)
    url = str(secret.get("url") or channel.config.get("url") or "")
    headers = {**(channel.config.get("headers") or {}), **(secret.get("headers") or {})}
    return url, {str(k): str(v) for k, v in headers.items()}


def url_display(url: str) -> str:
    """What the UI and the audit trail may show of a webhook URL: scheme and host only."""
    from urllib.parse import urlsplit

    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}/…" if p.scheme and p.hostname else ""


async def migrate_plaintext_webhooks(db: Any) -> int:
    """Move webhook URLs and headers that older versions kept in ``config`` into the encrypted
    secret (idempotent; called at startup)."""
    from sqlalchemy import select

    moved = 0
    for ch in (await db.execute(select(NotificationChannel).where(NotificationChannel.kind == "webhook"))).scalars():
        cfg = dict(ch.config or {})
        if "url" not in cfg and "headers" not in cfg:
            continue
        secret = open_secret(ch)
        if cfg.get("url"):
            secret["url"] = cfg["url"]
        if cfg.get("headers"):
            secret["headers"] = {**cfg["headers"], **(secret.get("headers") or {})}
        ch.secret = seal_secret(ch.id, secret)
        ch.config = {
            "url_display": url_display(str(cfg.get("url") or "")),
            "header_names": sorted(secret.get("headers") or {}),
            **{k: v for k, v in cfg.items() if k not in ("url", "headers", "url_display", "header_names")},
        }
        moved += 1
    return moved


# ---- SSRF guard -------------------------------------------------------------------------------
def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"))
    )


def validate_webhook_url(url: str) -> None:
    """Static checks at configuration time (scheme, host present, no credentials in the URL)."""
    parts = urlsplit(url or "")
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValidationFailed("webhook URL must be an absolute http(s) URL")
    if parts.username or parts.password:
        raise ValidationFailed("do not put credentials in the webhook URL; use the signing secret or headers")


async def check_target(url: str) -> None:
    """Resolve the webhook host and refuse non-public addresses (unless explicitly allowed)."""
    validate_webhook_url(url)
    if get_settings().webhook_allow_private:
        return
    host = urlsplit(url).hostname or ""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise DeliveryError(f"cannot resolve webhook host {host!r}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _is_public(ip):
            raise DeliveryError(
                f"webhook target {host!r} resolves to a non-public address ({ip}); "
                "set DBM_WEBHOOK_ALLOW_PRIVATE=true to allow internal receivers"
            )


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---- payloads ---------------------------------------------------------------------------------
def payload(event: str, alert: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "source": "dbmanager",
        "event": event,  # firing | resolved | test
        "sent_at": datetime.now(UTC).isoformat(),
        "alert": alert,
    }


_SEVERITY_COLOR = {"critical": "#b42318", "high": "#d9480f", "medium": "#b7791f", "low": "#2563eb", "info": "#64748b"}


def preset_payload(preset: str, event: str, alert: dict[str, Any] | None) -> dict[str, Any]:
    """Body for the channel's preset. ``generic`` is the documented DBManager JSON payload."""
    if preset == "generic":
        return payload(event, alert)
    subject = _subject(event, alert).removeprefix("[DBManager] ")
    text = _text(event, alert)
    url = (alert or {}).get("url")
    facts = []
    if alert:
        facts = [
            ("Server", f"{alert.get('server_name')} ({alert.get('engine')})"),
            ("Environment", alert.get("environment") or "—"),
            ("Severity", str(alert.get("severity"))),
            ("Status", "renotification" if alert.get("renotify") else event),
        ]
        if alert.get("value") is not None:
            facts.append(("Value", f"{alert['value']:g} ({alert.get('operator') or ''} {alert.get('threshold')})"))
    if preset == "slack":
        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text", "text": subject[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": (alert or {}).get("message") or text}},
        ]
        if facts:
            blocks.append({"type": "section", "fields": [{"type": "mrkdwn", "text": f"*{k}*\n{v}"} for k, v in facts]})
        if url:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {"type": "button", "text": {"type": "plain_text", "text": "Open in DBManager"}, "url": url}
                    ],
                }
            )
        return {"text": subject, "blocks": blocks}
    if preset == "teams":
        body: list[dict[str, Any]] = [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "wrap": True, "text": subject},
            {"type": "TextBlock", "wrap": True, "text": (alert or {}).get("message") or text},
        ]
        if facts:
            body.append({"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in facts]})
        card: dict[str, Any] = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": body,
        }
        if url:
            card["actions"] = [{"type": "Action.OpenUrl", "title": "Open in DBManager", "url": url}]
        return {
            "type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}],
        }
    raise DeliveryError(f"unknown webhook preset {preset!r}")


def _subject(event: str, alert: dict[str, Any] | None) -> str:
    if not alert:
        return "[DBManager] Test notification"
    state = "RESOLVED" if event == "resolved" else alert.get("severity", "").upper()
    again = " (still firing)" if alert.get("renotify") else ""
    return f"[DBManager] {state}: {alert.get('title')}{again}"


def _text(event: str, alert: dict[str, Any] | None) -> str:
    if not alert:
        return "This is a test notification from DBManager. The channel is configured correctly."
    lines = [
        alert.get("title", ""),
        "",
        alert.get("message", ""),
        "",
        f"Server: {alert.get('server_name')} ({alert.get('engine')})",
        f"Severity: {alert.get('severity')}",
        f"Status: {event}",
    ]
    if alert.get("value") is not None:
        lines.append(f"Value: {alert['value']} (threshold {alert.get('operator', '')} {alert.get('threshold')})")
    if alert.get("url"):
        lines.append(f"Open: {alert['url']}")
    return "\n".join(lines)


# ---- senders ----------------------------------------------------------------------------------
async def send_webhook(channel: NotificationChannel, body: dict[str, Any]) -> int:
    url, extra = webhook_target(channel)
    await check_target(url)
    data = json.dumps(body, default=str).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "dbmanager-notifications"}
    for k, v in extra.items():
        if k.lower() not in ("host", "content-length"):
            headers[k] = v
    secret = open_secret(channel).get("signing_secret")
    if secret:
        headers[SIGNATURE_HEADER] = sign(secret, data)
    timeout = get_settings().webhook_timeout_seconds
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            r = await client.post(url, content=data, headers=headers)
    except httpx.HTTPError as exc:
        raise DeliveryError(f"{type(exc).__name__}: {exc}") from exc
    if r.status_code >= 300:
        raise DeliveryError(f"receiver answered HTTP {r.status_code}", r.status_code)
    return r.status_code


def _smtp_settings(channel: NotificationChannel) -> dict[str, Any]:
    s = get_settings()
    c = channel.config
    secret = open_secret(channel)
    return {
        "host": c.get("smtp_host") or s.smtp_host,
        "port": int(c.get("smtp_port") or s.smtp_port),
        "user": c.get("smtp_user") or s.smtp_user,
        "password": secret.get("smtp_password") or s.smtp_password.get_secret_value(),
        "sender": c.get("from") or s.smtp_from,
        "security": smtp_security(c),
        "ca_file": s.smtp_ca_file,
        "timeout": s.smtp_timeout_seconds,
    }


def smtp_security(config: dict[str, Any]) -> str:
    """Channel setting ``security`` (or legacy ``starttls``) over the platform default."""
    s = get_settings()
    if config.get("security") in SMTP_SECURITY:
        return str(config["security"])
    if "starttls" in config:
        return "starttls" if config["starttls"] else "none"
    if s.smtp_security:
        return s.smtp_security
    return "starttls" if s.smtp_starttls else "none"


def _tls_context(ca_file: str) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_file or None)


def _send_mail_sync(
    cfg: dict[str, Any],
    recipients: list[str],
    subject: str,
    text: str,
    attachment: tuple[str, bytes, str] | None = None,
) -> None:
    msg = EmailMessage()
    msg["From"] = cfg["sender"]
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(text)
    if attachment is not None:
        name, data, media_type = attachment
        maintype, _, subtype = media_type.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=name)
    if cfg["security"] == "tls":  # implicit TLS (SMTPS)
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            cfg["host"], cfg["port"], timeout=cfg["timeout"], context=_tls_context(cfg["ca_file"])
        )
    else:
        client = smtplib.SMTP(cfg["host"], cfg["port"], timeout=cfg["timeout"])
    with client as smtp:
        if cfg["security"] == "starttls":
            smtp.starttls(context=_tls_context(cfg["ca_file"]))
        if cfg["user"]:
            smtp.login(cfg["user"], cfg["password"])
        smtp.send_message(msg)


async def send_email(channel: NotificationChannel, event: str, alert: dict[str, Any] | None) -> int:
    cfg = _smtp_settings(channel)
    recipients = [r for r in channel.config.get("recipients") or [] if r]
    if not cfg["host"] or not cfg["sender"]:
        raise DeliveryError("SMTP is not configured (DBM_SMTP_HOST / DBM_SMTP_FROM or the channel's SMTP settings)")
    if not recipients:
        raise DeliveryError("the channel has no recipients")
    try:
        await asyncio.to_thread(_send_mail_sync, cfg, recipients, _subject(event, alert), _text(event, alert))
    except (smtplib.SMTPException, OSError) as exc:
        raise DeliveryError(f"{type(exc).__name__}: {exc}") from exc
    return 250


async def send_file_email(
    channel: NotificationChannel, subject: str, text: str, attachment: tuple[str, bytes, str]
) -> int:
    """E-mail a file (scheduled reports) to an e-mail channel's recipients. Returns the number of
    recipients. Raises DeliveryError."""
    if channel.kind != "email":
        raise DeliveryError("only e-mail channels can receive report files")
    cfg = _smtp_settings(channel)
    recipients = [r for r in channel.config.get("recipients") or [] if r]
    if not cfg["host"] or not cfg["sender"]:
        raise DeliveryError("SMTP is not configured (DBM_SMTP_HOST / DBM_SMTP_FROM or the channel's SMTP settings)")
    if not recipients:
        raise DeliveryError("the channel has no recipients")
    try:
        await asyncio.to_thread(_send_mail_sync, cfg, recipients, subject, text, attachment)
    except (smtplib.SMTPException, OSError) as exc:
        raise DeliveryError(f"{type(exc).__name__}: {exc}") from exc
    return len(recipients)


async def send(channel: NotificationChannel, event: str, alert: dict[str, Any] | None) -> int | None:
    if channel.kind == "webhook":
        return await send_webhook(channel, preset_payload(channel.preset or "generic", event, alert))
    if channel.kind == "email":
        return await send_email(channel, event, alert)
    raise DeliveryError(f"unknown channel kind {channel.kind!r}")
