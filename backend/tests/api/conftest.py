from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from app.simulation.profiles import PROFILES, SimulationProfile, register_profile

ADMIN = ("admin", "Admin-Passw0rd!2026")
DEMO_PW = "Demo-Passw0rd!2026"

# A compact profile for the test suite: 9 servers, one day of coarse history.
CI_PROFILE = PROFILES.get("ci") or register_profile(
    SimulationProfile(
        key="ci",
        label="CI",
        description="test suite fleet",
        environments={"Production": 3, "DR": 1, "UAT": 2, "Development": 2, "Test": 1},
        applications=5,
        history_days=1,
        query_history_hours=6,
        resolution=((6, 5), (24, 15)),
        include_cloud=False,
    )
)
CI_SEED = 1  # PG-PROD-01, ORA-PROD-01 (critical), DB2-PROD-01, PG-DR-01, MSSQL-UAT-01, MYSQL-UAT-01,
#              PG-DEV-01, ORA-DEV-01, DB2-TEST-01 (unavailable)

# test accounts: username -> [(role, scope type, environment name | server name | "*")]
TEST_USERS = {
    "monitor": [("Monitoring User", "environment", "Production")],
    "dba.dev": [("DBA Administrator", "environment", "Development")],
    "perf.oracle": [("Performance Engineer", "server", "ORA-PROD-01"), ("Log Viewer", "server", "ORA-PROD-01")],
    "auditor": [("Security Auditor", "platform", "*")],
}


async def _db_available() -> bool:
    import asyncpg

    from app.core.config import get_settings

    url = get_settings().database_url.get_secret_value().replace("+asyncpg", "")
    try:
        conn = await asyncio.wait_for(asyncpg.connect(url), 5)
    except Exception:  # noqa: BLE001
        return False
    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await conn.close()
    return True


async def ensure_ci_fleet() -> None:
    """Make sure the deterministic CI simulation is present (tests may remove/regenerate it)."""
    from app.db.session import session_scope
    from app.simulation import service

    async with session_scope() as db:
        st = await service.status(db)
    if st["servers"] == CI_PROFILE.servers and st["profile"] == "ci" and st["seed"] == CI_SEED:
        return
    async with session_scope() as db:
        op = "regenerate" if st["servers"] else "generate"
        await service.request_generation(db, None, profile="ci", seed=CI_SEED, operation=op)
    await service.execute_pending()


async def ensure_test_users() -> None:
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.session import session_scope
    from app.models import Environment, Role, RoleAssignment, Server, User

    async with session_scope() as db:
        roles = {r.name: r.id for r in (await db.execute(select(Role))).scalars()}
        envs = {e.name: str(e.id) for e in (await db.execute(select(Environment))).scalars()}
        servers = {s.name: str(s.id) for s in (await db.execute(select(Server))).scalars()}
        for username, grants in TEST_USERS.items():
            u = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
            if u is None:
                u = User(
                    username=username, display_name=username, auth_source="local", password_hash=hash_password(DEMO_PW)
                )
                db.add(u)
                await db.flush()
            for role, stype, ref in grants:
                sid = envs[ref] if stype == "environment" else servers[ref] if stype == "server" else "*"
                exists = (
                    await db.execute(
                        select(RoleAssignment.id).where(
                            RoleAssignment.principal_id == u.id,
                            RoleAssignment.role_id == roles[role],
                            RoleAssignment.scope_type == stype,
                            RoleAssignment.scope_id == sid,
                        )
                    )
                ).first()
                if not exists:
                    db.add(
                        RoleAssignment(
                            principal_type="user",
                            principal_id=u.id,
                            role_id=roles[role],
                            scope_type=stype,
                            scope_id=sid,
                        )
                    )


@pytest_asyncio.fixture(scope="session")
async def app():  # type: ignore[no-untyped-def]
    if not await _db_available():
        pytest.skip("test PostgreSQL database not available")
    from app.core.config import get_settings
    from app.db.session import dispose_engine
    from app.main import create_app, startup_tasks

    get_settings.cache_clear()
    await startup_tasks()
    await ensure_ci_fleet()
    await ensure_test_users()
    application = create_app(with_lifespan=False)
    yield application
    await dispose_engine()


class Client(httpx.AsyncClient):
    async def login(self, username: str, password: str) -> httpx.Response:
        r = await self.post("/api/v1/auth/login", json={"username": username, "password": password})
        if r.status_code == 200:
            self.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        return r


@pytest_asyncio.fixture(scope="session")
async def make_client(app):  # type: ignore[no-untyped-def]
    clients: list[Client] = []

    async def factory(user: tuple[str, str] | None = None) -> Client:
        c = Client(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
        clients.append(c)
        if user:
            r = await c.login(*user)
            assert r.status_code == 200, r.text
        return c

    yield factory
    for c in clients:
        await c.aclose()


@pytest_asyncio.fixture(scope="session")
async def admin(make_client) -> AsyncIterator[Client]:  # type: ignore[no-untyped-def]
    yield await make_client(ADMIN)


@pytest_asyncio.fixture
async def servers(admin) -> dict[str, dict]:  # type: ignore[no-untyped-def]
    """Current servers by name (re-created CI fleet if a test removed it)."""
    await ensure_ci_fleet()
    await ensure_test_users()
    r = await admin.get("/api/v1/servers", params={"page_size": 500, "source": "all"})
    return {s["name"]: s for s in r.json()["items"]}
