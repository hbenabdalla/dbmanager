"""Personal API tokens: create, use with Bearer, scopes, interactive-only endpoints, revoke,
expiry, disabled owners, administration and audit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")

USER = ("tok.user", "Token-User-Passw0rd!")


def _code(r: httpx.Response) -> str | None:
    try:
        return r.json()["error"]["code"]
    except Exception:  # noqa: BLE001
        return None


@pytest.fixture
async def token_user(admin, servers):  # type: ignore[no-untyped-def]
    users = {u["username"]: u for u in (await admin.get("/api/v1/users", params={"page_size": 200})).json()["items"]}
    if USER[0] in users:
        uid = users[USER[0]]["id"]
        await admin.patch(f"/api/v1/users/{uid}", json={"is_active": True})
    else:
        r = await admin.post(
            "/api/v1/users", json={"username": USER[0], "password": USER[1], "must_change_password": False}
        )
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        roles = {x["name"]: x["id"] for x in (await admin.get("/api/v1/roles")).json()}
        envs = {e["name"]: e["id"] for e in (await admin.get("/api/v1/environments")).json()}
        r = await admin.post(
            "/api/v1/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": uid,
                "role_id": roles["Monitoring User"],
                "scope_type": "environment",
                "scope_id": envs["Production"],
            },
        )
        assert r.status_code == 201, r.text
    return uid


def bearer(app_client: httpx.AsyncClient, token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_token_lifecycle(make_client, admin, servers, token_user) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(USER)
    policy = (await c.get("/api/v1/me/tokens/policy")).json()
    assert policy["max_days"] == 365 and "server.view" in policy["available_scopes"]
    assert "users.manage" not in policy["available_scopes"]

    # cannot exceed own permissions, expiry is bounded
    r = await c.post("/api/v1/me/tokens", json={"name": "too much", "expires_in_days": 30, "scopes": ["users.manage"]})
    assert r.status_code == 403
    r = await c.post("/api/v1/me/tokens", json={"name": "forever", "expires_in_days": 4000})
    assert r.status_code == 422

    r = await c.post("/api/v1/me/tokens", json={"name": "ci-reader", "expires_in_days": 30})
    assert r.status_code == 201, r.text
    created = r.json()
    token, tid = created["token"], created["id"]
    assert token.startswith(f"dbm_{created['prefix']}_") and created["status"] == "active"

    listed = await c.get("/api/v1/me/tokens")
    assert listed.status_code == 200 and token not in listed.text and token.split("_")[2] not in listed.text
    assert any(t["id"] == tid and t["last_used_at"] is None for t in listed.json())

    # use it: no cookies, no CSRF header
    async with httpx.AsyncClient(transport=c._transport, base_url="http://testserver") as api:  # noqa: SLF001
        h = bearer(api, token)
        r = await api.get("/api/v1/servers", headers=h)
        assert r.status_code == 200
        names = {s["name"] for s in r.json()["items"]}
        assert "PG-PROD-01" in names and "PG-DEV-01" not in names  # owner's scope applies
        me = await api.get("/api/v1/auth/me", headers=h)
        assert me.status_code == 200 and me.json()["username"] == USER[0]
        # unsafe methods are not CSRF-checked for tokens (permission decides)
        r = await api.post("/api/v1/alerts/evaluate", headers=h)
        assert _code(r) != "csrf_failed" and r.status_code in (403, 200, 202)
        # interactive-only endpoints
        for method, path in (
            ("GET", "/api/v1/me/tokens"),
            ("POST", "/api/v1/me/tokens"),
            ("POST", "/api/v1/auth/change-password"),
            ("POST", "/api/v1/users"),
        ):
            r = await api.request(method, path, headers=h, json={})
            assert r.status_code == 403 and _code(r) == "interactive_only", (method, path, r.text)
        # garbage and unknown tokens
        r = await api.get("/api/v1/servers", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401
        r = await api.get("/api/v1/servers", headers={"Authorization": "Bearer dbm_aaaaaaaaaa_" + "A" * 43})
        assert r.status_code == 401

    assert (await c.get("/api/v1/me/tokens")).json()[0]["last_used_at"] is not None

    # revoke → 401
    assert (await c.delete(f"/api/v1/me/tokens/{tid}")).status_code == 200
    async with httpx.AsyncClient(transport=c._transport, base_url="http://testserver") as api:  # noqa: SLF001
        assert (await api.get("/api/v1/servers", headers=bearer(api, token))).status_code == 401

    # audit: create/revoke/failure recorded, the secret never
    events = (await admin.get("/api/v1/audit", params={"page_size": 200})).json()["items"]
    actions = {e["action"] for e in events}
    assert {"api_token.create", "api_token.revoke", "auth.api_token"} <= actions
    audit_text = (await admin.get("/api/v1/audit", params={"page_size": 200})).text
    assert token not in audit_text and token.split("_")[2] not in audit_text


async def test_scopes_restrict_permissions(make_client, servers, token_user) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(USER)
    r = await c.post(
        "/api/v1/me/tokens", json={"name": "inventory-only", "expires_in_days": 7, "scopes": ["server.view"]}
    )
    assert r.status_code == 201
    token = r.json()["token"]
    pg = servers["PG-PROD-01"]["id"]
    async with httpx.AsyncClient(transport=c._transport, base_url="http://testserver") as api:  # noqa: SLF001
        h = bearer(api, token)
        assert (await api.get(f"/api/v1/servers/{pg}", headers=h)).status_code == 200
        # the owner may view performance on Production, the token may not
        assert (await c.get(f"/api/v1/servers/{pg}/metrics/latest")).status_code == 200
        r = await api.get(f"/api/v1/servers/{pg}/metrics/latest", headers=h)
        assert r.status_code == 403


async def test_expired_token_and_disabled_owner(make_client, admin, token_user) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import update

    from app.db.session import session_scope
    from app.models import ApiToken

    c = await make_client(USER)
    t1 = (await c.post("/api/v1/me/tokens", json={"name": "short", "expires_in_days": 1})).json()
    t2 = (await c.post("/api/v1/me/tokens", json={"name": "owner-off", "expires_in_days": 1})).json()
    async with session_scope() as db:
        await db.execute(
            update(ApiToken).where(ApiToken.id == t1["id"]).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
    async with httpx.AsyncClient(transport=c._transport, base_url="http://testserver") as api:  # noqa: SLF001
        assert (await api.get("/api/v1/servers", headers=bearer(api, t1["token"]))).status_code == 401
        assert (await api.get("/api/v1/servers", headers=bearer(api, t2["token"]))).status_code == 200
        assert (await admin.patch(f"/api/v1/users/{token_user}", json={"is_active": False})).status_code == 200
        assert (await api.get("/api/v1/servers", headers=bearer(api, t2["token"]))).status_code == 401
    await admin.patch(f"/api/v1/users/{token_user}", json={"is_active": True})
    c = await make_client(USER)  # disabling the account also ended its sessions
    statuses = {t["name"]: t["status"] for t in (await c.get("/api/v1/me/tokens")).json()}
    assert statuses["short"] == "expired"


async def test_ip_allow_list(make_client, token_user) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(USER)
    r = await c.post(
        "/api/v1/me/tokens", json={"name": "office", "expires_in_days": 1, "allowed_cidrs": ["10.0.0.0/8"]}
    )
    assert r.status_code == 201 and r.json()["allowed_cidrs"] == ["10.0.0.0/8"]
    assert (
        await c.post("/api/v1/me/tokens", json={"name": "bad", "expires_in_days": 1, "allowed_cidrs": ["nope"]})
    ).status_code == 422
    async with httpx.AsyncClient(transport=c._transport, base_url="http://testserver") as api:  # noqa: SLF001
        assert (await api.get("/api/v1/servers", headers=bearer(api, r.json()["token"]))).status_code == 401
        # the test client's peer is a trusted proxy (127.0.0.1): a client-supplied leftmost
        # X-Forwarded-For value must not satisfy the allow-list ...
        spoofed = {**bearer(api, r.json()["token"]), "X-Forwarded-For": "10.1.2.3, 203.0.113.9"}
        assert (await api.get("/api/v1/servers", headers=spoofed)).status_code == 401
        # ... while the address appended by the trusted proxy does
        allowed = {**bearer(api, r.json()["token"]), "X-Forwarded-For": "203.0.113.9, 10.1.2.3"}
        assert (await api.get("/api/v1/servers", headers=allowed)).status_code == 200


async def test_admin_token_management(make_client, admin, token_user) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(USER)
    tid = (await c.post("/api/v1/me/tokens", json={"name": "admin-revokes", "expires_in_days": 1})).json()["id"]
    monitor = await make_client(("monitor", DEMO_PW))
    assert (await monitor.get(f"/api/v1/users/{token_user}/tokens")).status_code == 403
    assert (await monitor.delete(f"/api/v1/users/{token_user}/tokens/{tid}")).status_code == 403
    r = await admin.get(f"/api/v1/users/{token_user}/tokens")
    assert r.status_code == 200 and any(t["id"] == tid for t in r.json()) and "token" not in r.json()[0]
    assert (await admin.delete(f"/api/v1/users/{token_user}/tokens/{tid}")).status_code == 200
    assert {t["id"]: t["status"] for t in (await c.get("/api/v1/me/tokens")).json()}[tid] == "revoked"
    # another user's token is invisible through /me
    assert (await monitor.delete(f"/api/v1/me/tokens/{tid}")).status_code == 404
