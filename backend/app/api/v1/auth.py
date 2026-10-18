from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import CSRF_COOKIE, DB, SESSION_COOKIE, CurrentPrincipal, client_ip
from app.core.config import get_settings
from app.core.errors import AppError, NotAuthenticated, NotFound, ValidationFailed
from app.core.security import hash_password, verify_password
from app.models import IdentityProvider
from app.schemas.common import Message
from app.schemas.identity import ChangePasswordRequest, GrantOut, LoginRequest, MeOut, ProviderPublic
from app.services import audit, auth, oidc
from app.services.labels import scope_labels

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookies(response: Response, issued: auth.IssuedSession) -> None:
    s = get_settings()
    max_age = s.session_ttl_minutes * 60
    response.set_cookie(
        SESSION_COOKIE,
        issued.token,
        max_age=max_age,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
        path="/api",
        domain=s.cookie_domain,
    )
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf,
        max_age=max_age,
        httponly=False,
        secure=s.cookie_secure,
        samesite="strict",
        path="/",
        domain=s.cookie_domain,
    )


def _clear_cookies(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(SESSION_COOKIE, path="/api", domain=s.cookie_domain)
    response.delete_cookie(CSRF_COOKIE, path="/", domain=s.cookie_domain)


async def _me(db: DB, p: CurrentPrincipal, csrf: str | None = None) -> MeOut:
    labels = await scope_labels(db, [(g.scope.type.value, g.scope.id) for g in p.authz.grants])
    return MeOut(
        id=p.user.id,
        username=p.user.username,
        display_name=p.user.display_name,
        email=p.user.email,
        auth_source=p.user.auth_source,
        must_change_password=p.user.must_change_password,
        permissions=sorted(p.authz.all_permissions()),
        platform_permissions=sorted(p.authz.permissions_for()),
        grants=[
            GrantOut(
                role=g.role_name,
                scope_type=g.scope.type.value,
                scope_id=g.scope.id,
                scope_label=labels.get((g.scope.type.value, g.scope.id)),
                via_group=g.via_group,
            )
            for g in p.authz.grants
        ],
        csrf_token=csrf,
    )


@router.post("/login", response_model=MeOut, summary="Sign in with a local or LDAP account")
async def login(body: LoginRequest, request: Request, response: Response, db: DB) -> MeOut:
    ip = client_ip(request)
    user, source = await auth.authenticate(db, body.username, body.password, ip)
    issued = await auth.create_session(db, user, source, ip, request.headers.get("user-agent"))
    await db.commit()
    _set_cookies(response, issued)
    await audit.record("auth.login", username=user.username, auth_source=source, source_ip=ip, category="auth")
    from app.services.authz import Authorizer
    from app.services.principal import Principal

    p = Principal(user=user, authz=await Authorizer.load(db, user), auth_source=source, source_ip=ip, request_id=None)
    return await _me(db, p, csrf=issued.csrf)


@router.post("/logout", response_model=Message)
async def logout(request: Request, response: Response, db: DB, p: CurrentPrincipal) -> Message:
    await auth.revoke_session(db, request.cookies.get(SESSION_COOKIE))
    _clear_cookies(response)
    await audit.record("auth.logout", actor=p, category="auth")
    return Message(message="Signed out")


@router.get("/me", response_model=MeOut)
async def me(request: Request, db: DB, p: CurrentPrincipal) -> MeOut:
    return await _me(db, p)


@router.post("/change-password", response_model=Message)
async def change_password(body: ChangePasswordRequest, db: DB, p: CurrentPrincipal) -> Message:
    if p.user.auth_source != "local":
        raise ValidationFailed("Passwords for directory accounts are managed by your identity provider")
    if not verify_password(body.current_password, p.user.password_hash):
        await audit.record(
            "auth.change_password",
            actor=p,
            result="failure",
            category="auth",
            failure_reason="current password incorrect",
        )
        raise ValidationFailed("Current password is incorrect")
    if body.new_password == body.current_password:
        raise ValidationFailed("New password must differ from the current one")
    auth.validate_new_password(body.new_password)
    from datetime import UTC, datetime

    p.user.password_hash = hash_password(body.new_password)
    p.user.must_change_password = False
    p.user.password_changed_at = datetime.now(UTC)
    await audit.record("auth.change_password", actor=p, category="auth")
    return Message(message="Password changed")


@router.get("/providers", response_model=list[ProviderPublic], summary="Enabled single sign-on providers")
async def providers(db: DB) -> list[ProviderPublic]:
    rows = (
        (
            await db.execute(
                select(IdentityProvider).where(IdentityProvider.enabled.is_(True), IdentityProvider.kind == "oidc")
            )
        )
        .scalars()
        .all()
    )
    return [ProviderPublic(id=r.id, kind=r.kind, name=r.name) for r in rows]


async def _oidc_provider(db: DB, provider_id: uuid.UUID) -> IdentityProvider:
    p = await db.get(IdentityProvider, provider_id)
    if p is None or p.kind != "oidc" or not p.enabled:
        raise NotFound("Identity provider not found")
    return p


@router.get("/oidc/{provider_id}/start", include_in_schema=True, summary="Begin OIDC sign-in (redirect)")
async def oidc_start(provider_id: uuid.UUID, db: DB) -> RedirectResponse:
    provider = await _oidc_provider(db, provider_id)
    url, sealed = await oidc.start(provider)
    resp = RedirectResponse(url, status_code=302)
    s = get_settings()
    resp.set_cookie(
        oidc.FLOW_COOKIE,
        sealed,
        max_age=600,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/api/v1/auth/oidc",
    )
    return resp


@router.get("/oidc/{provider_id}/callback", include_in_schema=True, summary="OIDC redirect target")
async def oidc_callback(
    provider_id: uuid.UUID,
    request: Request,
    db: DB,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    base = get_settings().public_base_url.rstrip("/")
    ip = client_ip(request)
    if error or not code or not state:
        return RedirectResponse(f"{base}/login?error=sso_cancelled", status_code=302)
    provider = await _oidc_provider(db, provider_id)
    try:
        user = await oidc.callback(db, provider, code, state, request.cookies.get(oidc.FLOW_COOKIE))
    except (NotAuthenticated, AppError) as exc:
        await audit.record(
            "auth.login",
            result="failure",
            username=None,
            source_ip=ip,
            category="auth",
            auth_source="oidc",
            failure_reason=exc.message,
            details={"provider": provider.name},
        )
        return RedirectResponse(f"{base}/login?error=sso_failed", status_code=302)
    issued = await auth.create_session(db, user, "oidc", ip, request.headers.get("user-agent"))
    await db.commit()
    resp = RedirectResponse(f"{base}/", status_code=302)
    _set_cookies(resp, issued)
    resp.delete_cookie(oidc.FLOW_COOKIE, path="/api/v1/auth/oidc")
    await audit.record(
        "auth.login",
        username=user.username,
        auth_source="oidc",
        source_ip=ip,
        category="auth",
        details={"provider": provider.name},
    )
    return resp
