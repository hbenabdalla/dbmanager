"""TOTP and recovery codes (sprint 0.11.6), OIDC amr/acr enforcement."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pyotp
import pytest

from app.core.errors import NotAuthenticated, ValidationFailed
from app.services import mfa, oidc

T0 = 1_800_000_000.0  # a step boundary (30 s)


def user(**kw):  # type: ignore[no-untyped-def]
    base = dict(
        id=uuid.uuid4(),
        username="alice",
        auth_source="local",
        totp_secret=None,
        totp_enabled=False,
        totp_confirmed_at=None,
        totp_last_step=None,
        mfa_recovery=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _enrolled():  # type: ignore[no-untyped-def]
    u = user()
    out = mfa.begin_enrollment(u)
    assert out["otpauth_uri"].startswith("otpauth://totp/") and "issuer=DB%20Manager" in out["otpauth_uri"]
    assert out["qr_svg"].startswith("<svg") and out["secret"] not in str(u.totp_secret)  # stored encrypted
    return u, pyotp.TOTP(out["secret"])


def test_code_accepted_once_within_one_step_of_skew() -> None:
    u, totp = _enrolled()
    code = totp.at(T0)
    assert mfa.check_totp(u, code, T0 + 29)
    assert not mfa.check_totp(u, code, T0 + 29)  # replay of the same code
    assert mfa.check_totp(u, totp.at(T0 + 60), T0 + 30)  # next step ahead (clock skew) is fine
    assert not mfa.check_totp(u, totp.at(T0), T0 + 120)  # too old and before the last used step
    assert not mfa.check_totp(u, "12345", T0) and not mfa.check_totp(u, "abcdef", T0)


def test_enrollment_confirmation_and_recovery_codes() -> None:
    u, totp = _enrolled()
    with pytest.raises(ValidationFailed):
        mfa.confirm_enrollment(u, "000000")
    codes = mfa.confirm_enrollment(u, totp.now())
    assert u.totp_enabled and len(codes) == 10 and len(set(codes)) == 10
    assert all(c not in str(u.mfa_recovery) for c in codes)  # only hashes are stored
    assert mfa.verify_second_factor(u, codes[0].lower()) == "recovery"
    with pytest.raises(NotAuthenticated):
        mfa.verify_second_factor(u, codes[0])  # consumed
    assert len(u.mfa_recovery) == 9
    mfa.disable(u)
    assert not u.totp_enabled and u.totp_secret is None and u.mfa_recovery == []


def test_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    s = SimpleNamespace(mfa_required_for="none", mfa_issuer="DB Manager")
    monkeypatch.setattr(mfa, "get_settings", lambda: s)
    admin_perms, plain = {"users.manage", "server.view"}, {"server.view"}
    assert not mfa.required(user(), admin_perms)
    s.mfa_required_for = "admins"
    assert mfa.required(user(), admin_perms) and not mfa.required(user(), plain)
    assert not mfa.required(user(auth_source="ldap"), admin_perms)  # directory accounts: the IdP's job
    s.mfa_required_for = "all"
    assert mfa.enrollment_pending(user(), plain) and not mfa.enrollment_pending(user(totp_enabled=True), plain)
    assert mfa.path_allowed_while_pending("/api/v1/me/mfa/totp/setup")
    assert not mfa.path_allowed_while_pending("/api/v1/servers")


def test_challenge_round_trip_and_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    u = user()
    token = mfa.challenge(u, "local")
    assert mfa.open_challenge(token)["uid"] == str(u.id)
    with pytest.raises(NotAuthenticated):
        mfa.open_challenge(token[:-4] + "AAAA")
    monkeypatch.setattr(mfa.time, "time", lambda: 10**12)
    with pytest.raises(NotAuthenticated, match="expired"):
        mfa.open_challenge(token)


def test_oidc_requires_amr_or_acr_when_configured() -> None:
    oidc.check_mfa({}, {"amr": ["pwd"]})  # not required: anything goes
    cfg = {"require_mfa": True}
    oidc.check_mfa(cfg, {"amr": ["pwd", "otp"]})
    oidc.check_mfa(cfg, {"amr": "mfa"})
    with pytest.raises(NotAuthenticated, match="multi-factor"):
        oidc.check_mfa(cfg, {"amr": ["pwd"]})
    with pytest.raises(NotAuthenticated):
        oidc.check_mfa(cfg, {})
    oidc.check_mfa({**cfg, "required_acr": ["urn:mace:incommon:iap:silver", "2"]}, {"acr": "2"})
    oidc.check_mfa({**cfg, "mfa_amr_values": ["duo"]}, {"amr": ["duo"]})
