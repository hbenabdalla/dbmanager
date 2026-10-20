"""TLS settings for DBManager's own connections: the control-plane PostgreSQL (asyncpg) and the
time-series storage (httpx). Monitored databases have their own per-server TLS (adapters/tls.py).

PostgreSQL modes follow libpq's ``sslmode`` semantics:

* ``disable`` / ``prefer`` — passed to asyncpg as is (``prefer`` encrypts when the server offers it);
* ``require`` — encrypted, the certificate is not checked;
* ``verify-ca`` — the certificate must chain to ``DBM_DATABASE_TLS_CA_FILE`` (or the system CAs);
* ``verify-full`` — as ``verify-ca`` and the host name must match the certificate.

An empty mode keeps whatever ``DBM_DATABASE_URL`` asks for (``?ssl=…``), asyncpg's default otherwise."""

from __future__ import annotations

import ssl
from typing import Any

from app.core.config import Settings, get_settings


def _context(ca_file: str, *, verify: bool, hostname: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=ca_file or None)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = hostname
    ctx.verify_mode = ssl.CERT_REQUIRED if verify else ssl.CERT_NONE
    return ctx


def database_ssl(s: Settings | None = None) -> ssl.SSLContext | str | None:
    """The ``ssl`` connect argument for asyncpg, or ``None`` to leave the URL in charge."""
    s = s or get_settings()
    mode = s.database_tls_mode
    if not mode:
        return None
    if mode in ("disable", "prefer"):
        return mode
    if mode == "require":
        return _context("", verify=False, hostname=False)
    return _context(s.database_tls_ca_file, verify=True, hostname=mode == "verify-full")


def database_connect_args(application_name: str, s: Settings | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {"server_settings": {"application_name": application_name}}
    tls = database_ssl(s)
    if tls is not None:
        args["ssl"] = tls
    return args


def timeseries_verify(s: Settings | None = None) -> bool | str:
    """httpx ``verify`` for an https time-series URL: a private CA bundle, or the system CAs."""
    s = s or get_settings()
    return s.timeseries_tls_ca_file or True
