"""Backup / restore of the control-plane database, as documented in
docs/operations/backup-restore.md: pg_dump -Fc of a migrated, populated database restored into an
empty one keeps the schema revision, the inventory and the encrypted credentials — which decrypt
again with the same DBM_MASTER_KEY (and with no other key)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import uuid

import pytest
from sqlalchemy.engine import make_url

from tests.migrations.test_migrations import URL, _exec, _pg_available, alembic

SRC, DST = "dbm_migration_test", "dbm_restore_test"

pytestmark = [
    pytest.mark.skipif(not _pg_available(), reason="test PostgreSQL (DBM_TEST_DATABASE_URL) not available"),
    pytest.mark.skipif(
        not shutil.which("pg_dump") or not shutil.which("pg_restore"), reason="pg_dump/pg_restore not installed"
    ),
]


def _url(db: str) -> str:
    return make_url(URL).set(database=db).render_as_string(hide_password=False)


def _libpq_env() -> dict[str, str]:
    u = make_url(URL)
    return {
        **os.environ,
        "PGHOST": u.host or "localhost",
        "PGPORT": str(u.port or 5432),
        "PGUSER": u.username or "",
        "PGPASSWORD": u.password or "",
    }


async def _populate() -> uuid.UUID:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.core.security import hash_password
    from app.models import Organization, Server, User
    from app.services.secrets import set_credential

    engine = create_async_engine(_url(SRC))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            org = Organization(name="Backup Org", slug="backup-org")
            db.add(org)
            await db.flush()
            s = Server(organization_id=org.id, name="restored-pg", engine="postgresql", host="db.internal", port=5432)
            db.add(s)
            await db.flush()
            await set_credential(db, s, "monitoring", username="dbm_monitor", password="Backup-Restore-Secret-1")
            db.add(User(username="restore.user", password_hash=hash_password("Restore-User-Passw0rd!")))
            await db.commit()
            return s.id
    finally:
        await engine.dispose()


async def _read_back(server_id: uuid.UUID) -> tuple[str, str | None]:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.models import Credential, Server
    from app.services.secrets import resolve_password

    engine = create_async_engine(_url(DST))
    try:
        async with AsyncSession(engine) as db:
            s = await db.get(Server, server_id)
            cred = (await db.execute(select(Credential).where(Credential.server_id == server_id))).scalar_one()
            return s.name, resolve_password(cred)
    finally:
        await engine.dispose()


async def _restored_login_and_revision() -> tuple[bool, str]:
    """The restored user can still sign in (password hash intact); the schema revision is kept."""
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.core.security import verify_password
    from app.models import User

    engine = create_async_engine(_url(DST))
    try:
        async with AsyncSession(engine) as db:
            u = (await db.execute(select(User).where(User.username == "restore.user"))).scalar_one()
            rev = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            return verify_password("Restore-User-Passw0rd!", u.password_hash), rev
    finally:
        await engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def databases():  # type: ignore[no-untyped-def]
    for db in (SRC, DST):
        asyncio.run(_exec(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)"))
    asyncio.run(_exec(f"CREATE DATABASE {SRC}"))
    asyncio.run(_exec(f"CREATE DATABASE {DST}"))
    yield
    for db in (SRC, DST):
        asyncio.run(_exec(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)"))


def test_dump_and_restore_keep_schema_inventory_and_secrets(tmp_path) -> None:  # type: ignore[no-untyped-def]
    alembic("upgrade", "head")  # migrates SRC (the module's scratch database)
    server_id = asyncio.run(_populate())
    dump = tmp_path / "dbmanager.dump"
    env = _libpq_env()
    subprocess.run(["pg_dump", "-Fc", "-d", SRC, "-f", str(dump)], env=env, check=True, timeout=120)  # noqa: S603, S607
    subprocess.run(["pg_restore", "--no-owner", "-d", DST, str(dump)], env=env, check=True, timeout=120)  # noqa: S603, S607
    name, password = asyncio.run(_read_back(server_id))
    assert name == "restored-pg"
    assert password == "Backup-Restore-Secret-1"  # same DBM_MASTER_KEY → decrypts
    can_login, revision = asyncio.run(_restored_login_and_revision())
    assert can_login
    from tests.migrations.test_migrations import revisions

    assert revision == revisions()[-1]

    # the stored blob is useless without the key: a different master key cannot decrypt it
    import base64

    from app.core import crypto
    from app.core.config import get_settings

    old = os.environ["DBM_MASTER_KEY"]
    os.environ["DBM_MASTER_KEY"] = base64.b64encode(b"z" * 32).decode()
    get_settings.cache_clear()
    crypto.reset_keyring()
    try:
        with pytest.raises(crypto.CryptoError):
            asyncio.run(_read_back(server_id))
    finally:
        os.environ["DBM_MASTER_KEY"] = old
        get_settings.cache_clear()
        crypto.reset_keyring()
