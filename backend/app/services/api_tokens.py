"""Personal API tokens.

Format: ``dbm_<prefix>_<secret>`` — ``prefix`` (10 lowercase base32 chars) identifies the row and
is safe to display; ``secret`` (43 url-safe chars, 256 bits) is only ever shown once. The database
keeps SHA-256(secret); lookups go by prefix, comparison is constant-time.

A token acts as its owner, never more: the effective permissions of a request are the owner's
*current* grants intersected with the token's scopes (evaluated per request, so revoking a role
takes effect immediately). Token requests skip CSRF (no ambient cookie authority) but may not call
the interactive-only endpoints in :data:`INTERACTIVE_ONLY`.
"""

from __future__ import annotations

import base64
import ipaddress
import re
import secrets
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotAuthenticated, PermissionDenied, ValidationFailed
from app.core.security import constant_time_equals, token_hash
from app.domain.permissions import ALL_PERMISSIONS
from app.models import ApiToken, User
from app.services.authz import Authorizer

TOKEN_RE = re.compile(r"^dbm_([a-z2-7]{10})_([A-Za-z0-9_-]{43})$")
USE_UPDATE_INTERVAL = timedelta(seconds=60)

#: (methods, path regex) a token may never call: token management, own credentials and the
#: management of users, groups, roles, role assignments and identity providers. ``*`` = any method.
INTERACTIVE_ONLY: list[tuple[str, re.Pattern[str]]] = [
    ("*", re.compile(r"^/api/v1/me/tokens(/.*)?$")),
    ("*", re.compile(r"^/api/v1/users/[^/]+/tokens(/.*)?$")),
    ("*", re.compile(r"^/api/v1/auth/(change-password|logout)$")),
    ("*", re.compile(r"^/api/v1/users/[^/]+/reset-password$")),
    ("W", re.compile(r"^/api/v1/(users|groups|roles|role-assignments|identity-providers)(/.*)?$")),
]
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def generate() -> tuple[str, str, str]:
    """Return (full token, prefix, secret hash)."""
    prefix = base64.b32encode(secrets.token_bytes(10)).decode().lower()[:10]
    secret = secrets.token_urlsafe(32)[:43].ljust(43, "A")
    return f"dbm_{prefix}_{secret}", prefix, token_hash(secret)


def parse(token: str) -> tuple[str, str] | None:
    m = TOKEN_RE.match(token.strip())
    return (m.group(1), m.group(2)) if m else None


def is_interactive_only(method: str, path: str) -> bool:
    for methods, rx in INTERACTIVE_ONLY:
        if rx.match(path) and (methods == "*" or (methods == "W" and method.upper() in WRITE_METHODS)):
            return True
    return False


def restrict(authz: Authorizer, scopes: list[str] | None) -> Authorizer:
    """Intersect every grant with the token's scopes (None = no further restriction)."""
    if scopes is None:
        return authz
    allowed = frozenset(scopes)
    return Authorizer(
        user_id=authz.user_id, grants=[replace(g, permissions=g.permissions & allowed) for g in authz.grants]
    )


def validate_cidrs(cidrs: list[str] | None) -> list[str] | None:
    if not cidrs:
        return None
    out = []
    for c in cidrs:
        try:
            out.append(str(ipaddress.ip_network(c.strip(), strict=False)))
        except ValueError as exc:
            raise ValidationFailed(f"invalid CIDR {c!r}") from exc
    return out


def ip_allowed(cidrs: list[str] | None, ip: str | None) -> bool:
    if not cidrs:
        return True
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(c, strict=False) for c in cidrs)


def status(t: ApiToken, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if t.revoked_at:
        return "revoked"
    if t.expires_at <= now:
        return "expired"
    return "active"


async def create(
    db: AsyncSession,
    owner: User,
    owner_authz: Authorizer,
    *,
    name: str,
    expires_in_days: int,
    scopes: list[str] | None,
    allowed_cidrs: list[str] | None,
    created_by: str,
) -> tuple[ApiToken, str]:
    max_days = get_settings().api_token_max_days
    if not 1 <= expires_in_days <= max_days:
        raise ValidationFailed(f"expires_in_days must be between 1 and {max_days}")
    name = name.strip()
    if not name or len(name) > 128:
        raise ValidationFailed("name is required (at most 128 characters)")
    if scopes is not None:
        unknown = sorted(set(scopes) - ALL_PERMISSIONS)
        if unknown:
            raise ValidationFailed(f"unknown permissions: {', '.join(unknown)}")
        held = owner_authz.all_permissions()
        beyond = sorted(set(scopes) - held)
        if beyond:
            raise PermissionDenied(
                f"a token cannot carry permissions you do not hold: {', '.join(beyond)}",
                details={"permissions": beyond},
            )
        if not scopes:
            raise ValidationFailed("select at least one permission, or omit scopes to inherit all of yours")
        scopes = sorted(set(scopes))
    full, prefix, secret_hash = generate()
    tok = ApiToken(
        id=uuid.uuid4(),
        user_id=owner.id,
        name=name,
        prefix=prefix,
        secret_hash=secret_hash,
        scopes=scopes,
        allowed_cidrs=validate_cidrs(allowed_cidrs),
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
        created_by=created_by,
    )
    db.add(tok)
    await db.flush()
    return tok, full


async def authenticate(db: AsyncSession, raw: str, source_ip: str | None) -> tuple[ApiToken, User]:
    """Resolve a Bearer token or raise :class:`NotAuthenticated` (one generic message)."""
    parsed = parse(raw)
    generic = NotAuthenticated("Invalid or expired API token", code="invalid_token")
    if parsed is None:
        raise generic
    prefix, secret = parsed
    tok = (await db.execute(select(ApiToken).where(ApiToken.prefix == prefix))).scalar_one_or_none()
    if tok is None or not constant_time_equals(token_hash(secret), tok.secret_hash):
        raise generic
    now = datetime.now(UTC)
    if status(tok, now) != "active":
        raise generic
    if not ip_allowed(tok.allowed_cidrs, source_ip):
        raise NotAuthenticated("API token not allowed from this address", code="invalid_token")
    user = await db.get(User, tok.user_id)
    if user is None or not user.is_active or (user.locked_until and user.locked_until > now):
        raise NotAuthenticated("Account disabled", code="invalid_token")
    if tok.last_used_at is None or now - tok.last_used_at > USE_UPDATE_INTERVAL:
        tok.last_used_at = now
        tok.last_used_ip = source_ip
        await db.commit()
    return tok, user
