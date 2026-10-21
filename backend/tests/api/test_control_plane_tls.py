"""Control-plane PostgreSQL over TLS (sprint 0.15.4), against the test server.

Needs a PostgreSQL that serves TLS with a certificate naming ``localhost`` but **not**
``127.0.0.1`` (``tests/integration/tls/make_certs.py --dns-only``), and its CA in
``DBM_TEST_DATABASE_TLS_CA`` (PEM). CI configures its PostgreSQL service that way
(``ci-backend.yml``); without the variable the tests are skipped."""

from __future__ import annotations

import os
import ssl

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.core.tlsclient import database_connect_args

CA = os.environ.get("DBM_TEST_DATABASE_TLS_CA")
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not CA, reason="DBM_TEST_DATABASE_TLS_CA not set: the test PostgreSQL serves no TLS"),
]


async def _connect(host: str, mode: str, ca: str = "") -> str:
    url = make_url(os.environ["DBM_TEST_DATABASE_URL"]).set(host=host)
    s = Settings(
        database_url=url.render_as_string(hide_password=False), database_tls_mode=mode, database_tls_ca_file=ca
    )  # type: ignore[arg-type]
    engine = create_async_engine(url, connect_args=database_connect_args("dbmanager-tls-test", s))
    try:
        async with engine.connect() as conn:
            return str((await conn.execute(text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"))).scalar())
    finally:
        await engine.dispose()


async def test_verified_tls_to_the_control_plane() -> None:
    assert CA
    assert await _connect("localhost", "require") == "True"
    assert await _connect("localhost", "verify-full", CA) == "True"
    assert await _connect("127.0.0.1", "verify-ca", CA) == "True"  # chain only, any host name
    # the certificate names localhost, not 127.0.0.1: verify-full refuses the address
    with pytest.raises(ssl.SSLCertVerificationError, match="(?i)hostname|ip address|match"):
        await _connect("127.0.0.1", "verify-full", CA)


async def test_a_wrong_ca_is_refused(tmp_path_factory) -> None:  # type: ignore[no-untyped-def]
    from tests.integration.test_mysql_family import _other_ca

    other = tmp_path_factory.mktemp("ca") / "other.pem"
    other.write_text(_other_ca())
    with pytest.raises(ssl.SSLCertVerificationError):
        await _connect("localhost", "verify-ca", str(other))
