"""API rate limiting: per-principal policies, 429 with Retry-After, sessions vs API tokens,
login limit, counters shared by replicas (database backend) and cleanup."""

from __future__ import annotations

import pytest

from tests.api.conftest import ADMIN

pytestmark = pytest.mark.asyncio(loop_scope="session")

EXPORT = "/api/v1/audit/export.csv"


@pytest.fixture
async def limits(app, monkeypatch):  # type: ignore[no-untyped-def]
    """Small limits for this test; counters cleared before and after."""
    from app.core.config import get_settings
    from app.services import ratelimit

    s = get_settings()
    assert s.rate_limit_backend == "database"

    def set_limits(**values: int) -> None:
        for name, value in values.items():
            monkeypatch.setattr(s, name, value)

    await ratelimit.backend().reset()
    yield set_limits
    await ratelimit.backend().reset()


def _error(r):  # type: ignore[no-untyped-def]
    return r.json()["error"]


async def test_expensive_operations_get_429_with_retry_after(admin, limits) -> None:  # type: ignore[no-untyped-def]
    limits(api_rate_expensive_per_minute=3)
    for _ in range(3):
        assert (await admin.get(EXPORT)).status_code == 200
    r = await admin.get(EXPORT)
    assert r.status_code == 429
    err = _error(r)
    assert err["code"] == "rate_limited" and err["details"]["policy"] == "expensive"
    assert err["details"]["limit"] == 3 and err["details"]["window_seconds"] == 60
    retry = int(r.headers["Retry-After"])
    assert 1 <= retry <= 120 and err["details"]["retry_after_seconds"] == retry
    assert r.headers["RateLimit-Remaining"] == "0"
    assert r.headers["RateLimit-Policy"] == '"expensive";q=3;w=60'
    assert f"{retry} s" in err["message"]
    # ordinary requests of the same user are not affected by the expensive policy
    assert (await admin.get("/api/v1/servers")).status_code == 200


async def test_live_target_queries_have_their_own_budget(admin, servers, limits) -> None:  # type: ignore[no-untyped-def]
    limits(api_rate_target_per_minute=2, api_rate_expensive_per_minute=1000)
    sid = servers["PG-PROD-01"]["id"]
    statuses = [(await admin.get(f"/api/v1/servers/{sid}/sessions")).status_code for _ in range(2)]
    assert 429 not in statuses
    r = await admin.get(f"/api/v1/servers/{sid}/locks")  # same policy, other live endpoint
    assert r.status_code == 429 and _error(r)["details"]["policy"] == "target"
    # stored telemetry is not a live query
    assert (await admin.get(f"/api/v1/servers/{sid}/metrics/latest")).status_code != 429


async def test_general_api_limit_per_principal(make_client, limits) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(ADMIN)
    limits(api_rate_per_user_per_minute=4)
    codes = [(await c.get("/api/v1/servers")).status_code for _ in range(6)]
    assert codes[:4] == [200] * 4 and codes[4:] == [429, 429]
    assert _error(await c.get("/api/v1/servers"))["details"]["policy"] == "api"


async def test_api_tokens_are_counted_separately_from_sessions(admin, make_client, limits) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post("/api/v1/me/tokens", json={"name": "rate-limit test", "expires_in_days": 1})
    assert r.status_code == 201, r.text
    token, tid = r.json()["token"], r.json()["id"]
    try:
        limits(api_rate_expensive_per_minute=2)
        for _ in range(2):
            assert (await admin.get(EXPORT)).status_code == 200
        assert (await admin.get(EXPORT)).status_code == 429
        api = await make_client()
        auth = {"Authorization": f"Bearer {token}"}
        assert (await api.get(EXPORT, headers=auth)).status_code == 200
        assert (await api.get(EXPORT, headers=auth)).status_code == 200
        r = await api.get(EXPORT, headers=auth)
        assert r.status_code == 429 and "Retry-After" in r.headers
    finally:
        limits(api_rate_expensive_per_minute=1000)
        assert (await admin.delete(f"/api/v1/me/tokens/{tid}")).status_code == 200


async def test_disabled_policy_never_refuses(admin, limits) -> None:  # type: ignore[no-untyped-def]
    limits(api_rate_expensive_per_minute=0)
    for _ in range(5):
        assert (await admin.get(EXPORT)).status_code == 200


async def test_login_limit_per_source_ip(make_client, limits) -> None:  # type: ignore[no-untyped-def]
    limits(login_rate_per_ip_per_minute=2)
    c = await make_client()
    for _ in range(2):
        r = await c.login("nobody-here", "Wrong-Passw0rd!")
        assert r.status_code == 401
    r = await c.login(*ADMIN)  # even correct credentials wait for the window
    assert r.status_code == 429 and _error(r)["details"]["policy"] == "login"
    assert int(r.headers["Retry-After"]) >= 1


async def test_failed_token_authentications_are_limited(make_client, limits) -> None:  # type: ignore[no-untyped-def]
    limits(api_token_auth_failures_per_minute=2)
    c = await make_client()
    bad = {"Authorization": "Bearer dbm_aaaaaaaaaa_" + "A" * 43}
    assert [(await c.get("/api/v1/servers", headers=bad)).status_code for _ in range(2)] == [401, 401]
    r = await c.get("/api/v1/servers", headers=bad)
    assert r.status_code == 429 and _error(r)["details"]["policy"] == "token_failures"


async def test_counters_are_shared_by_replicas(app, limits) -> None:  # type: ignore[no-untyped-def]
    """Two backend instances (two replicas) using the same database see one budget; the
    in-memory backend, by contrast, counts per process."""
    from sqlalchemy import text

    from app.db.session import get_engine
    from app.services.ratelimit import DatabaseBackend, MemoryBackend, Policy

    t = [1_799_999_940.0]  # a window boundary

    def clock() -> float:
        return t[0]

    p = Policy("expensive", 3)
    a, b = DatabaseBackend(clock), DatabaseBackend(clock)
    results = [(await x.hit("user:shared", p)).allowed for x in (a, b, a, b)]
    assert results == [True, True, True, False]
    m1, m2 = MemoryBackend(clock), MemoryBackend(clock)
    assert [(await x.hit("user:shared", p)).allowed for x in (m1, m2, m1, m2)] == [True] * 4

    async def windows() -> int:
        async with get_engine().connect() as conn:
            q = text("SELECT count(*) FROM rate_limit_windows WHERE bucket = 'user:shared'")
            return int((await conn.execute(q)).scalar_one())

    t[0] += 119  # late in the next window: the full previous window weighs only 1/60
    assert (await a.hit("user:shared", p)).allowed
    t[0] += 60
    assert (await b.hit("user:shared", p)).allowed
    assert await windows() == 2  # current + previous only; older windows are pruned


async def test_retention_removes_idle_buckets(app, limits) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    from app.db.session import get_engine
    from app.services import ratelimit

    async with get_engine().begin() as conn:
        await conn.execute(
            text("INSERT INTO rate_limit_windows (bucket, window_start, hits) VALUES ('api:user:idle', 60, 5)")
        )
    assert await ratelimit.purge_idle() >= 1
    async with get_engine().connect() as conn:
        left = (
            await conn.execute(text("SELECT count(*) FROM rate_limit_windows WHERE bucket = 'api:user:idle'"))
        ).scalar()
    assert left == 0


async def test_concurrent_hits_never_exceed_the_limit(app, limits) -> None:  # type: ignore[no-untyped-def]
    """Many replicas hitting the last slots at the same moment: exactly ``limit`` succeed."""
    import asyncio

    from app.services.ratelimit import DatabaseBackend, Policy

    p = Policy("expensive", 5)
    replicas = [DatabaseBackend(lambda: 1_799_999_950.0) for _ in range(4)]
    decisions = await asyncio.gather(*(replicas[i % 4].hit("user:race", p) for i in range(24)))
    assert sum(d.allowed for d in decisions) == 5
    assert all(d.retry_after >= 1 for d in decisions if not d.allowed)
