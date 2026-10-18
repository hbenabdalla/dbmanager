"""MFA end to end (sprint 0.11.6): enrollment, two-step sign-in with an authenticator or a
recovery code, the "admins must enroll" policy, administrator reset, API tokens refused."""

from __future__ import annotations

import time
import uuid

import pyotp
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")
PW = "Mfa-User-Passw0rd!"


async def _user(admin, *, platform_admin: bool = False) -> str:  # type: ignore[no-untyped-def]
    name = f"mfa.{uuid.uuid4().hex[:6]}"
    r = await admin.post("/api/v1/users", json={"username": name, "password": PW, "must_change_password": False})
    assert r.status_code == 201, r.text
    if platform_admin:
        roles = {x["name"]: x["id"] for x in (await admin.get("/api/v1/roles")).json()}
        r = await admin.post(
            "/api/v1/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": r.json()["id"],
                "role_id": roles["Platform Administrator"],
                "scope_type": "platform",
            },
        )
        assert r.status_code == 201, r.text
    return name


def _next_code(secret: str) -> str:
    """A code for the next 30-second step (the current one may already have been used)."""
    return pyotp.TOTP(secret).at(time.time() + 30)


async def test_enroll_and_sign_in_with_totp_and_recovery_code(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    name = await _user(admin)
    c = await make_client((name, PW))
    assert (await c.get("/api/v1/me/mfa")).json()["enabled"] is False
    assert (await c.post("/api/v1/me/mfa/totp/setup", json={"password": "wrong"})).status_code == 401
    setup = (await c.post("/api/v1/me/mfa/totp/setup", json={"password": PW})).json()
    secret = setup["secret"]
    assert setup["qr_svg"].startswith("<svg")
    assert (await c.post("/api/v1/me/mfa/totp/enable", json={"code": "000000"})).status_code == 422
    r = await c.post("/api/v1/me/mfa/totp/enable", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    recovery = r.json()["recovery_codes"]
    assert len(recovery) == 10
    me = (await c.get("/api/v1/auth/me")).json()
    assert me["mfa_enabled"] is True

    # password alone no longer creates a session
    fresh = await make_client()
    step1 = await fresh.post("/api/v1/auth/login", json={"username": name, "password": PW})
    assert step1.status_code == 200 and step1.json()["mfa_required"] is True
    assert "dbm_session" not in fresh.cookies
    token = step1.json()["mfa_token"]
    bad = await fresh.post("/api/v1/auth/login/mfa", json={"mfa_token": token, "code": "123456"})
    assert bad.status_code == 401
    ok = await fresh.post("/api/v1/auth/login/mfa", json={"mfa_token": token, "code": _next_code(secret)})
    assert ok.status_code == 200, ok.text
    assert ok.json()["username"] == name and "dbm_session" in fresh.cookies

    # a recovery code works once
    again = await make_client()
    t2 = (await again.post("/api/v1/auth/login", json={"username": name, "password": PW})).json()["mfa_token"]
    assert (await again.post("/api/v1/auth/login/mfa", json={"mfa_token": t2, "code": recovery[0]})).status_code == 200
    third = await make_client()
    t3 = (await third.post("/api/v1/auth/login", json={"username": name, "password": PW})).json()["mfa_token"]
    assert (await third.post("/api/v1/auth/login/mfa", json={"mfa_token": t3, "code": recovery[0]})).status_code == 401
    assert (await c.get("/api/v1/me/mfa")).json()["recovery_codes_left"] == 9

    events = (await admin.get("/api/v1/audit", params={"page_size": 100, "q": name})).json()["items"]
    actions = {(e["action"], e["result"]) for e in events}
    assert ("auth.mfa_enable", "success") in actions and ("auth.login", "failure") in actions

    # administrator reset for a lost device: password sign-in works again without a code
    uid = next(u["id"] for u in (await admin.get("/api/v1/users", params={"q": name})).json()["items"])
    assert (await admin.post(f"/api/v1/users/{uid}/mfa/reset")).status_code == 200
    plain = await make_client()
    r = await plain.post("/api/v1/auth/login", json={"username": name, "password": PW})
    assert r.status_code == 200 and r.json().get("mfa_required") is not True


async def test_admins_must_enroll_before_anything_else(admin, make_client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    name = await _user(admin, platform_admin=True)
    plain_name = await _user(admin)
    # from here on the bootstrap admin is also an un-enrolled administrator: it is blocked too
    monkeypatch.setattr(get_settings(), "mfa_required_for", "admins")
    assert (await admin.get("/api/v1/servers")).status_code == 403
    c = await make_client((name, PW))
    me = (await c.get("/api/v1/auth/me")).json()
    assert me["mfa_enrollment_required"] is True
    blocked = await c.get("/api/v1/servers")
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "mfa_enrollment_required"
    secret = (await c.post("/api/v1/me/mfa/totp/setup", json={"password": PW})).json()["secret"]
    assert (await c.post("/api/v1/me/mfa/totp/enable", json={"code": pyotp.TOTP(secret).now()})).status_code == 200
    assert (await c.get("/api/v1/servers")).status_code == 200
    # the policy does not touch non-admin users
    plain = await make_client((plain_name, PW))
    assert (await plain.get("/api/v1/auth/me")).json()["mfa_enrollment_required"] is False


async def test_mfa_endpoints_refuse_api_tokens(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post("/api/v1/me/tokens", json={"name": "mfa probe", "expires_in_days": 1})
    token, tid = r.json()["token"], r.json()["id"]
    api = await make_client()
    res = await api.post(
        "/api/v1/me/mfa/totp/setup", json={"password": "x"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 403 and res.json()["error"]["code"] == "interactive_only"
    await admin.delete(f"/api/v1/me/tokens/{tid}")
