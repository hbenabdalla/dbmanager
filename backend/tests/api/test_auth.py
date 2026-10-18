import pytest

from tests.api.conftest import ADMIN

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_login_success_sets_secure_cookies(make_client) -> None:  # type: ignore[no-untyped-def]
    c = await make_client()
    r = await c.login(*ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin" and "users.manage" in body["platform_permissions"]
    cookie = r.headers.get_list("set-cookie")
    session = next(h for h in cookie if h.startswith("dbm_session="))
    assert "HttpOnly" in session and "SameSite=strict" in session.replace("Strict", "strict")
    assert "Admin-Passw0rd" not in r.text and "password_hash" not in r.text


async def test_login_failure_is_generic(make_client) -> None:  # type: ignore[no-untyped-def]
    c = await make_client()
    r1 = await c.login("admin", "wrong-password")
    r2 = await c.login("no-such-user", "wrong-password")
    assert r1.status_code == r2.status_code == 401
    assert r1.json()["error"]["message"] == r2.json()["error"]["message"]


async def test_unauthenticated_requests_rejected(make_client) -> None:  # type: ignore[no-untyped-def]
    c = await make_client()
    assert (await c.get("/api/v1/servers")).status_code == 401
    assert (await c.get("/api/v1/dashboard/summary")).status_code == 401


async def test_csrf_required_for_unsafe_methods(make_client) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(ADMIN)
    token = c.headers.pop("X-CSRF-Token")
    r = await c.post("/api/v1/groups", json={"name": "csrf-test"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    c.headers["X-CSRF-Token"] = "forged"
    assert (await c.post("/api/v1/groups", json={"name": "csrf-test"})).status_code == 403
    c.headers["X-CSRF-Token"] = token
    assert (await c.post("/api/v1/groups", json={"name": "csrf-test"})).status_code == 201


async def test_lockout_after_repeated_failures(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post(
        "/api/v1/users", json={"username": "lockme", "password": "Lock-Me-Passw0rd!", "must_change_password": False}
    )
    assert r.status_code == 201
    c = await make_client()
    for _ in range(5):
        assert (await c.login("lockme", "nope")).status_code == 401
    # correct password is now refused too (locked)
    assert (await c.login("lockme", "Lock-Me-Passw0rd!")).status_code == 401
    uid = r.json()["id"]
    assert (await admin.patch(f"/api/v1/users/{uid}", json={"unlock": True})).status_code == 200
    assert (await c.login("lockme", "Lock-Me-Passw0rd!")).status_code == 200


async def test_logout_revokes_session(make_client) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(ADMIN)
    assert (await c.post("/api/v1/auth/logout")).status_code == 200
    assert (await c.get("/api/v1/auth/me")).status_code == 401


async def test_password_policy_enforced(admin) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post("/api/v1/users", json={"username": "weakpw", "password": "password"})
    assert r.status_code == 422 and "password" in r.json()["error"]["details"]


async def test_health_endpoints(make_client) -> None:  # type: ignore[no-untyped-def]
    c = await make_client()
    assert (await c.get("/healthz")).json() == {"status": "ok"}
    assert (await c.get("/readyz")).json()["status"] == "ready"
    m = await c.get("/metrics")
    assert m.status_code == 200 and "dbm_http_requests_total" in m.text


async def test_security_headers(make_client) -> None:  # type: ignore[no-untyped-def]
    c = await make_client()
    r = await c.get("/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-request-id"]
