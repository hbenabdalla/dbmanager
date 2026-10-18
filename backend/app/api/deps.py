"""FastAPI dependencies: DB session, authenticated principal, CSRF, rate limiting."""

from __future__ import annotations

import ipaddress
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotAuthenticated, NotFound, PermissionDenied, RateLimited
from app.core.logging import user_ctx
from app.core.security import constant_time_equals, token_hash
from app.db.session import get_sessionmaker
from app.models import Server
from app.services import ratelimit
from app.services.auth import resolve_session
from app.services.authz import Authorizer, server_ref
from app.services.principal import Principal

SESSION_COOKIE = "dbm_session"
CSRF_COOKIE = "dbm_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


_MESSAGES = {
    "api": "Too many requests",
    "target": "Too many live requests to managed databases",
    "expensive": "Too many expensive operations (SQL execution, plans, exports, tests)",
}


async def enforce(policy: str, key: str, message: str | None = None) -> None:
    """Count one hit; raise ``429`` with ``Retry-After`` when ``key`` is over ``policy``."""
    d = await ratelimit.check(policy, key)
    if not d.allowed:
        raise refusal(policy, d, message)


def refusal(policy: str, d: ratelimit.Decision, message: str | None = None) -> RateLimited:
    return RateLimited(
        f"{message or _MESSAGES.get(policy, 'Too many requests')}. Try again in {d.retry_after} s.",
        details={
            "policy": policy,
            "limit": d.limit,
            "window_seconds": ratelimit.WINDOW_SECONDS,
            "retry_after_seconds": d.retry_after,
        },
        headers=ratelimit.headers(d, policy),
    )


def principal_key(p: Principal) -> str:
    """Rate-limit identity: one API token, or the user of an interactive session."""
    return f"token:{p.api_token_id}" if p.api_token_id else f"user:{p.user.id}"


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
    from app.services import api_tokens, audit

    ip = client_ip(request)
    fail_key = f"ip:{ip or '-'}"
    peek = await ratelimit.check("token_failures", fail_key, record=False)
    if not peek.allowed:
        raise refusal("token_failures", peek, "Too many failed API token authentications")
    try:
        tok, user = await api_tokens.authenticate(db, raw, ip)
    except NotAuthenticated as exc:
        await ratelimit.check("token_failures", fail_key)
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
    await enforce("api", f"token:{tok.id}")
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
    bearer = _bearer(request)
    if bearer is not None:
        return await _token_principal(request, db, bearer)
    token = request.cookies.get(SESSION_COOKIE)
    sess, user = await resolve_session(db, token)
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not constant_time_equals(token_hash(header), sess.csrf_hash):
            raise PermissionDenied("CSRF token missing or invalid", code="csrf_failed")
    await enforce("api", f"user:{user.id}")
    authz = await Authorizer.load(db, user)
    from app.services import mfa

    if mfa.enrollment_pending(user, authz.permissions_for()) and not mfa.path_allowed_while_pending(request.url.path):
        raise PermissionDenied(
            "Set up multi-factor authentication for your account first (Account → Security)",
            code="mfa_enrollment_required",
        )
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


def rate_limit(policy: str) -> Any:
    """Route dependency: count the request against ``policy`` for the calling principal
    (``target`` = live queries of a managed database, ``expensive`` = SQL execution, plans,
    administration, exports and tests). Used as ``dependencies=[rate_limit("expensive")]``."""

    async def _limit(p: CurrentPrincipal) -> None:
        await enforce(policy, principal_key(p))

    _limit.__name__ = f"rate_limit_{policy}"
    return Depends(_limit)


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
