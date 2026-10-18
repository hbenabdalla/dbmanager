"""FastAPI dependencies: DB session, authenticated principal, CSRF, rate limiting."""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotAuthenticated, NotFound, PermissionDenied, RateLimited
from app.core.logging import user_ctx
from app.core.ratelimit import SlidingWindowLimiter
from app.core.security import constant_time_equals, token_hash
from app.db.session import get_sessionmaker
from app.models import Server
from app.services.auth import resolve_session
from app.services.authz import Authorizer, server_ref
from app.services.principal import Principal

SESSION_COOKIE = "dbm_session"
CSRF_COOKIE = "dbm_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_user_limiter: SlidingWindowLimiter | None = None
_token_fail_limiter: SlidingWindowLimiter | None = None


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


DB = Annotated[AsyncSession, Depends(get_db)]


def client_ip(request: Request) -> str | None:
    peer = request.client.host if request.client else None
    trusted = get_settings().trusted_proxies
    xff = request.headers.get("x-forwarded-for")
    if peer and xff and _is_trusted(peer, trusted):
        return xff.split(",")[0].strip()[:64]
    return peer


def _is_trusted(peer: str, trusted: list[str]) -> bool:
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for t in trusted:
        try:
            if ip in ipaddress.ip_network(t, strict=False):
                return True
        except ValueError:
            continue
    return False


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    if h[:7].lower() == "bearer ":
        return h[7:].strip()
    return None


async def _token_principal(request: Request, db: AsyncSession, raw: str) -> Principal:
    """API-token authentication: no CSRF (no ambient cookie), interactive-only endpoints refused,
    permissions = owner's current grants ∩ token scopes."""
    global _token_fail_limiter, _user_limiter
    from app.services import api_tokens, audit

    ip = client_ip(request)
    if _token_fail_limiter is None:
        _token_fail_limiter = SlidingWindowLimiter(get_settings().api_token_auth_failures_per_minute, 60)
    if _token_fail_limiter.blocked(ip or "-"):
        raise RateLimited("Too many failed API token authentications")
    try:
        tok, user = await api_tokens.authenticate(db, raw, ip)
    except NotAuthenticated as exc:
        _token_fail_limiter.hit(ip or "-")
        parsed = api_tokens.parse(raw)
        await audit.record(
            "auth.api_token",
            result="failure",
            category="auth",
            source_ip=ip,
            auth_source="api_token",
            failure_reason=exc.message,
            details={"prefix": parsed[0] if parsed else None, "path": request.url.path},
        )
        raise
    if api_tokens.is_interactive_only(request.method, request.url.path):
        raise PermissionDenied(
            "This endpoint requires an interactive session and cannot be called with an API token",
            code="interactive_only",
        )
    if _user_limiter is None:
        _user_limiter = SlidingWindowLimiter(get_settings().api_rate_per_user_per_minute, 60)
    if not _user_limiter.hit(str(user.id)):
        raise RateLimited("Too many requests")
    authz = api_tokens.restrict(await Authorizer.load(db, user), tok.scopes)
    user_ctx.set(user.username)
    return Principal(
        user=user,
        authz=authz,
        auth_source="api_token",
        source_ip=ip,
        request_id=getattr(request.state, "request_id", None),
        api_token_id=tok.id,
    )


async def get_principal(request: Request, db: DB) -> Principal:
    global _user_limiter
    bearer = _bearer(request)
    if bearer is not None:
        return await _token_principal(request, db, bearer)
    token = request.cookies.get(SESSION_COOKIE)
    sess, user = await resolve_session(db, token)
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not constant_time_equals(token_hash(header), sess.csrf_hash):
            raise PermissionDenied("CSRF token missing or invalid", code="csrf_failed")
    if _user_limiter is None:
        _user_limiter = SlidingWindowLimiter(get_settings().api_rate_per_user_per_minute, 60)
    if not _user_limiter.hit(str(user.id)):
        raise RateLimited("Too many requests")
    authz = await Authorizer.load(db, user)
    user_ctx.set(user.username)
    return Principal(
        user=user,
        authz=authz,
        auth_source=sess.auth_source,
        source_ip=client_ip(request),
        request_id=getattr(request.state, "request_id", None),
        session_token_hash=sess.token_hash,
    )


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


async def load_server(
    db: AsyncSession, principal: Principal, server_id: uuid.UUID, permission: str = "server.view"
) -> Server:
    """Fetch a server and enforce visibility. Invisible servers look like 404 (no enumeration)."""
    server = await db.get(Server, server_id)
    if server is None:
        raise NotFound("Server not found")
    ref = server_ref(server)
    if not principal.authz.can_see("server.view", ref):
        raise NotFound("Server not found")
    if permission != "server.view" and not principal.authz.has(permission, ref):
        raise PermissionDenied(f"Missing permission '{permission}' for this server", details={"permission": permission})
    return server


def require_auth_cookie(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise NotAuthenticated("Authentication required")
    return token
