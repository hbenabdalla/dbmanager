"""Authentication: local accounts, LDAP delegation, session lifecycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotAuthenticated, RateLimited, ValidationFailed
from app.core.metrics import LOGIN_ATTEMPTS
from app.core.security import (
    hash_password,
    needs_rehash,
    new_token,
    password_policy_errors,
    token_hash,
    verify_password,
)
from app.models import IdentityProvider, User, UserSession
from app.services import audit

log = logging.getLogger(__name__)


@dataclass
class IssuedSession:
    token: str
    csrf: str
    expires_at: datetime
    user: User


GENERIC_LOGIN_ERROR = "Invalid username or password"


async def create_session(
    db: AsyncSession, user: User, auth_source: str, source_ip: str | None, user_agent: str | None
) -> IssuedSession:
    s = get_settings()
    token, csrf = new_token(), new_token(24)
    expires = datetime.now(UTC) + timedelta(minutes=s.session_ttl_minutes)
    db.add(
        UserSession(
            token_hash=token_hash(token),
            csrf_hash=token_hash(csrf),
            user_id=user.id,
            auth_source=auth_source,
            expires_at=expires,
            source_ip=source_ip,
            user_agent=(user_agent or "")[:500],
        )
    )
    user.last_login_at = datetime.now(UTC)
    # bound the number of concurrent sessions per user
    old = (
        (
            await db.execute(
                select(UserSession.token_hash)
                .where(UserSession.user_id == user.id)
                .order_by(UserSession.created_at.desc())
                .offset(20)
            )
        )
        .scalars()
        .all()
    )
    if old:
        await db.execute(delete(UserSession).where(UserSession.token_hash.in_(old)))
    await db.flush()
    return IssuedSession(token, csrf, expires, user)


async def resolve_session(db: AsyncSession, token: str | None) -> tuple[UserSession, User]:
    if not token:
        raise NotAuthenticated("Authentication required")
    sess = await db.get(UserSession, token_hash(token))
    now = datetime.now(UTC)
    if sess is None:
        raise NotAuthenticated("Session expired or invalid")
    idle = timedelta(minutes=get_settings().session_idle_timeout_minutes)
    if sess.expires_at <= now or now - sess.last_seen_at > idle:
        await db.delete(sess)
        await db.commit()
        raise NotAuthenticated("Session expired")
    user = await db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise NotAuthenticated("Account disabled")
    if now - sess.last_seen_at > timedelta(seconds=60):
        sess.last_seen_at = now
        await db.commit()
    return sess, user


async def revoke_session(db: AsyncSession, token: str | None) -> None:
    if token:
        await db.execute(delete(UserSession).where(UserSession.token_hash == token_hash(token)))


async def revoke_user_sessions(db: AsyncSession, user_id: object) -> None:
    await db.execute(delete(UserSession).where(UserSession.user_id == user_id))


async def purge_expired_sessions(db: AsyncSession) -> int:
    idle = timedelta(minutes=get_settings().session_idle_timeout_minutes)
    now = datetime.now(UTC)
    res = await db.execute(
        delete(UserSession).where((UserSession.expires_at < now) | (UserSession.last_seen_at < now - idle))
    )
    return res.rowcount or 0


# -------------------------------------------------------------------------------------
async def authenticate(db: AsyncSession, username: str, password: str, source_ip: str | None) -> tuple[User, str]:
    """Authenticate with local credentials or an enabled LDAP provider.

    Returns (user, auth_source). Raises NotAuthenticated with a generic message.
    """
    s = get_settings()
    from app.services import ratelimit

    login_limit = await ratelimit.check("login", f"ip:{source_ip}") if source_ip else None
    if login_limit is not None and not login_limit.allowed:
        LOGIN_ATTEMPTS.labels("any", "rate_limited").inc()
        await audit.record(
            "auth.login",
            result="denied",
            username=username,
            source_ip=source_ip,
            category="auth",
            failure_reason="rate limited",
        )
        raise RateLimited(
            f"Too many login attempts. Try again in {login_limit.retry_after} s.",
            details={"policy": "login", "retry_after_seconds": login_limit.retry_after},
            headers=ratelimit.headers(login_limit, "login"),
        )

    username = username.strip()
    user = (await db.execute(select(User).where(func.lower(User.username) == username.lower()))).scalar_one_or_none()
    now = datetime.now(UTC)

    if user and user.locked_until and user.locked_until > now:
        verify_password(password, None)  # keep timing uniform
        LOGIN_ATTEMPTS.labels(user.auth_source, "locked").inc()
        await audit.record(
            "auth.login",
            result="denied",
            username=user.username,
            source_ip=source_ip,
            category="auth",
            auth_source=user.auth_source,
            failure_reason="account locked",
        )
        raise NotAuthenticated(GENERIC_LOGIN_ERROR)

    if user is None or user.auth_source == "ldap":
        ldap_user = await _try_ldap(db, username, password)
        if ldap_user is not None:
            LOGIN_ATTEMPTS.labels("ldap", "success").inc()
            return ldap_user, "ldap"
        if user is None:
            verify_password(password, None)
            LOGIN_ATTEMPTS.labels("local", "failure").inc()
            await audit.record(
                "auth.login",
                result="failure",
                username=username,
                source_ip=source_ip,
                category="auth",
                failure_reason="unknown user or bad password",
            )
            raise NotAuthenticated(GENERIC_LOGIN_ERROR)

    if user.auth_source == "local" and user.is_active and verify_password(password, user.password_hash):
        if not user.totp_enabled:
            # with MFA the counter is reset only after the second factor succeeded
            # (api/v1/auth.login_mfa): wrong codes accumulate across password re-entries
            user.failed_logins = 0
            user.locked_until = None
        if user.password_hash and needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        LOGIN_ATTEMPTS.labels("local", "success").inc()
        return user, "local"

    user.failed_logins += 1
    reason = "bad password" if user.is_active else "account disabled"
    if user.failed_logins >= s.login_max_failures:
        user.locked_until = now + timedelta(minutes=s.login_lockout_minutes)
        reason += f"; account locked for {s.login_lockout_minutes} minutes"
    await db.commit()
    LOGIN_ATTEMPTS.labels(user.auth_source, "failure").inc()
    await audit.record(
        "auth.login",
        result="failure",
        username=user.username,
        source_ip=source_ip,
        category="auth",
        auth_source=user.auth_source,
        failure_reason=reason,
    )
    raise NotAuthenticated(GENERIC_LOGIN_ERROR)


async def _try_ldap(db: AsyncSession, username: str, password: str) -> User | None:
    providers = (
        (
            await db.execute(
                select(IdentityProvider).where(IdentityProvider.kind == "ldap", IdentityProvider.enabled.is_(True))
            )
        )
        .scalars()
        .all()
    )
    if not providers or not password:
        return None
    from app.services.ldap_auth import ldap_login

    for p in providers:
        try:
            user = await ldap_login(db, p, username, password)
        except Exception:  # noqa: BLE001
            log.exception("ldap provider error", extra={"provider": p.name})
            continue
        if user is not None:
            return user
    return None


def validate_new_password(password: str) -> None:
    errors = password_policy_errors(password)
    if errors:
        raise ValidationFailed("Password does not meet the policy", details={"password": errors})
