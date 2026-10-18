"""Multi-factor authentication for local accounts: TOTP (RFC 6238, via pyotp) plus one-time
recovery codes.

* The TOTP seed is envelope-encrypted with the platform keyring (AAD bound to the user) and is
  shown only once, during enrollment.
* A code is accepted at most once (``totp_last_step``) and within ±1 step (30 s) of clock skew.
* Recovery codes are random, shown once, stored as SHA-256 hashes and consumed on use.
* Sign-in with MFA is two-step: the password step returns a short-lived sealed challenge
  (no session); the code step turns it into a session.
* ``DBM_MFA_REQUIRED_FOR`` (none | admins | all) makes enrollment mandatory: such users can only
  reach the enrollment endpoints until they enroll.

Directory (LDAP) and single sign-on (OIDC) users are not enrolled here: for OIDC, MFA is
enforced by the identity provider and checked through the ``amr`` / ``acr`` claims
(``require_mfa`` in the provider configuration, see app.services.oidc).
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import UTC, datetime
from typing import Any

import pyotp

from app.core.config import get_settings
from app.core.crypto import CryptoError, EncryptedBlob, get_keyring
from app.core.errors import NotAuthenticated, ValidationFailed
from app.domain.permissions import PLATFORM_ONLY
from app.models import User

CHALLENGE_TTL_S = 300
RECOVERY_CODES = 10
STEP_S = 30
#: what an MFA-pending session may still call
ENROLLMENT_PATHS = (
    "/api/v1/auth/me",
    "/api/v1/auth/logout",
    "/api/v1/auth/change-password",
    "/api/v1/me/mfa",
    "/api/v1/about",
    "/api/v1/version",
)


def _aad(user: User) -> str:
    return f"totp:{user.id}"


def _hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().upper().encode()).hexdigest()


def required(user: User, platform_permissions: set[str]) -> bool:
    """Must this (local) user use MFA under the current policy?"""
    if user.auth_source != "local":
        return False
    policy = get_settings().mfa_required_for
    if policy == "all":
        return True
    if policy == "admins":
        return bool(set(platform_permissions) & PLATFORM_ONLY)
    return False


def enrollment_pending(user: User, platform_permissions: set[str]) -> bool:
    return required(user, platform_permissions) and not user.totp_enabled


def path_allowed_while_pending(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in ENROLLMENT_PATHS)


# ---- enrollment ------------------------------------------------------------------------------
def begin_enrollment(user: User) -> dict[str, Any]:
    """Create a new (not yet active) seed; returns it once with an otpauth URI and a QR code."""
    import segno

    seed = pyotp.random_base32()
    user.totp_secret = get_keyring().encrypt(seed, _aad(user)).to_dict()
    uri = pyotp.TOTP(seed).provisioning_uri(name=user.username, issuer_name=get_settings().mfa_issuer)
    qr = segno.make(uri, error="m").svg_inline(scale=4, dark="#111827", light="#ffffff")
    return {"secret": seed, "otpauth_uri": uri, "qr_svg": qr}


def _seed(user: User) -> str:
    if not user.totp_secret:
        raise ValidationFailed("No authenticator is being enrolled; start the enrollment first")
    try:
        return get_keyring().decrypt(EncryptedBlob.from_dict(user.totp_secret), _aad(user))
    except CryptoError as exc:
        raise ValidationFailed("The stored authenticator cannot be read (master key changed?); enroll again") from exc


def check_totp(user: User, code: str, now: float | None = None) -> bool:
    """Valid code for the current step ±1 that was not used before (replay guard)."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    totp = pyotp.TOTP(_seed(user))
    now = now if now is not None else time.time()
    current = int(now // STEP_S)
    for step in (current, current - 1, current + 1):
        if secrets.compare_digest(totp.at(step * STEP_S), code):
            if user.totp_last_step is not None and step <= user.totp_last_step:
                return False
            user.totp_last_step = step
            return True
    return False


def new_recovery_codes(user: User) -> list[str]:
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}".upper() for _ in range(RECOVERY_CODES)]
    user.mfa_recovery = [_hash(c) for c in codes]
    return codes


def use_recovery_code(user: User, code: str) -> bool:
    h = _hash(code)
    remaining = list(user.mfa_recovery or [])
    for stored in remaining:
        if secrets.compare_digest(stored, h):
            remaining.remove(stored)
            user.mfa_recovery = remaining
            return True
    return False


def confirm_enrollment(user: User, code: str) -> list[str]:
    if user.totp_enabled:
        raise ValidationFailed("An authenticator is already enrolled; disable it first to enroll a new one")
    if not check_totp(user, code):
        raise ValidationFailed("The code is not valid; check the time on your device and try again")
    user.totp_enabled, user.totp_confirmed_at = True, datetime.now(UTC)
    return new_recovery_codes(user)


def disable(user: User) -> None:
    user.totp_enabled, user.totp_secret, user.totp_confirmed_at, user.totp_last_step = False, None, None, None
    user.mfa_recovery = []


def verify_second_factor(user: User, code: str) -> str:
    """'totp' or 'recovery' when accepted; raises NotAuthenticated otherwise."""
    if not user.totp_enabled:
        raise NotAuthenticated("Multi-factor authentication is not enabled for this account")
    if check_totp(user, code):
        return "totp"
    if "-" in (code or "") or len((code or "").strip()) > 6:
        if use_recovery_code(user, code):
            return "recovery"
    raise NotAuthenticated("Invalid authentication code")


# ---- two-step sign-in -------------------------------------------------------------------------
def challenge(user: User, source: str) -> str:
    return get_keyring().seal_json({"uid": str(user.id), "src": source, "ts": time.time()}, "mfa-login")


def open_challenge(token: str) -> dict[str, Any]:
    try:
        data = get_keyring().open_json(token, "mfa-login")
    except CryptoError as exc:
        raise NotAuthenticated("Sign-in expired; enter your password again") from exc
    if time.time() - float(data.get("ts", 0)) > CHALLENGE_TTL_S:
        raise NotAuthenticated("Sign-in expired; enter your password again")
    return data
