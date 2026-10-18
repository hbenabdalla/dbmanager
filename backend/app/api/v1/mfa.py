"""Self-service multi-factor authentication (TOTP) for local accounts, and the administrator
reset for lost devices. Interactive sessions only (API tokens are refused)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentPrincipal
from app.core.errors import NotAuthenticated, NotFound, ValidationFailed
from app.core.security import verify_password
from app.models import User
from app.schemas.common import Message
from app.services import audit, mfa

router = APIRouter(tags=["mfa"])


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class PasswordCodeIn(PasswordIn):
    code: str = Field(min_length=6, max_length=32)


def _local(p: CurrentPrincipal) -> User:
    if p.user.auth_source != "local":
        raise ValidationFailed(
            "Multi-factor authentication for directory and single sign-on accounts is provided by your "
            "identity provider"
        )
    return p.user


def _password(user: User, password: str) -> None:
    if not verify_password(password, user.password_hash):
        raise NotAuthenticated("Password is incorrect")


@router.get("/me/mfa", summary="Multi-factor authentication status of the signed-in account")
async def status(p: CurrentPrincipal) -> dict[str, Any]:
    u = p.user
    return {
        "available": u.auth_source == "local",
        "enabled": u.totp_enabled,
        "confirmed_at": u.totp_confirmed_at,
        "recovery_codes_left": len(u.mfa_recovery or []),
        "required": mfa.required(u, p.authz.permissions_for()),
        "enrollment_pending": mfa.enrollment_pending(u, p.authz.permissions_for()),
    }


@router.post("/me/mfa/totp/setup", summary="Start authenticator enrollment (returns the secret once)")
async def setup(body: PasswordIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    u = _local(p)
    _password(u, body.password)
    if u.totp_enabled:
        raise ValidationFailed("An authenticator is already enrolled; disable it first to enroll a new one")
    out = mfa.begin_enrollment(u)
    await audit.record("auth.mfa_setup", actor=p, category="auth")
    return out


@router.post("/me/mfa/totp/enable", summary="Confirm enrollment with a code; returns recovery codes once")
async def enable(body: CodeIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    u = _local(p)
    codes = mfa.confirm_enrollment(u, body.code)
    await audit.record("auth.mfa_enable", actor=p, category="auth")
    return {"enabled": True, "recovery_codes": codes}


@router.post("/me/mfa/totp/disable", summary="Remove the authenticator (password and current code)")
async def disable(body: PasswordCodeIn, db: DB, p: CurrentPrincipal) -> Message:
    u = _local(p)
    _password(u, body.password)
    mfa.verify_second_factor(u, body.code)
    mfa.disable(u)
    await audit.record("auth.mfa_disable", actor=p, category="auth")
    note = " You must enroll again before you can continue." if mfa.required(u, p.authz.permissions_for()) else ""
    return Message(message="Authenticator removed." + note)


@router.post("/me/mfa/recovery-codes", summary="Replace the recovery codes (password and current code)")
async def regenerate(body: PasswordCodeIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    u = _local(p)
    _password(u, body.password)
    mfa.verify_second_factor(u, body.code)
    codes = mfa.new_recovery_codes(u)
    await audit.record("auth.mfa_recovery_codes", actor=p, category="auth")
    return {"recovery_codes": codes}


@router.post("/users/{user_id}/mfa/reset", summary="Remove a user's authenticator (lost device)")
async def admin_reset(user_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("users.manage")
    u = await db.get(User, user_id)
    if u is None:
        raise NotFound("User not found")
    mfa.disable(u)
    await audit.record("user.mfa_reset", actor=p, category="security", object_name=u.username)
    return Message(message=f"Authenticator of {u.username} removed; the user enrolls again at the next sign-in")
