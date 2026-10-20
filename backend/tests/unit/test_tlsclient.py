"""TLS settings for the control-plane PostgreSQL and the time-series storage."""

import ssl

from app.core.config import Settings
from app.core.tlsclient import database_connect_args, database_ssl, timeseries_verify


def _s(**kw: object) -> Settings:
    return Settings(database_url="postgresql+asyncpg://u:p@db/x", **kw)  # type: ignore[arg-type]


def test_empty_mode_leaves_the_url_in_charge() -> None:
    assert database_ssl(_s()) is None
    assert database_connect_args("dbmanager", _s()) == {"server_settings": {"application_name": "dbmanager"}}


def test_modes_follow_libpq_semantics() -> None:
    assert database_ssl(_s(database_tls_mode="disable")) == "disable"
    assert database_ssl(_s(database_tls_mode="prefer")) == "prefer"
    req = database_ssl(_s(database_tls_mode="require"))
    assert isinstance(req, ssl.SSLContext) and req.verify_mode == ssl.CERT_NONE and not req.check_hostname
    ca = database_ssl(_s(database_tls_mode="verify-ca"))
    assert isinstance(ca, ssl.SSLContext) and ca.verify_mode == ssl.CERT_REQUIRED and not ca.check_hostname
    full = database_ssl(_s(database_tls_mode="verify-full"))
    assert isinstance(full, ssl.SSLContext) and full.verify_mode == ssl.CERT_REQUIRED and full.check_hostname
    assert full.minimum_version >= ssl.TLSVersion.TLSv1_2
    args = database_connect_args("dbmanager", _s(database_tls_mode="require"))
    assert args["ssl"] is req or isinstance(args["ssl"], ssl.SSLContext)


def test_timeseries_verify_uses_a_private_ca_when_set() -> None:
    assert timeseries_verify(_s()) is True
    assert timeseries_verify(_s(timeseries_tls_ca_file="/etc/dbm/vm-ca.pem")) == "/etc/dbm/vm-ca.pem"
