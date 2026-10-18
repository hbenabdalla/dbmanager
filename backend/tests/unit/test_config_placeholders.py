import base64
import os

import pytest
from pydantic import SecretStr

from app.core.config import Settings, placeholder_settings
from app.core.crypto import CryptoError, _decode_key


def test_placeholders_are_reported():
    s = Settings(
        master_key=SecretStr("CHANGE_ME_base64_32_bytes"),
        bootstrap_admin_password=SecretStr("CHANGE_ME-Admin-2026!"),
        database_url=SecretStr("postgresql+asyncpg://u:CHANGE_ME_long_random_password@h/db"),
    )
    names = placeholder_settings(s)
    assert "DBM_MASTER_KEY" in names
    assert "DBM_BOOTSTRAP_ADMIN_PASSWORD" in names
    assert any(n.startswith("POSTGRES_PASSWORD") for n in names)
    assert "DBM_DEMO_USER_PASSWORD" not in names


def test_real_values_pass():
    s = Settings(master_key=SecretStr(base64.b64encode(os.urandom(32)).decode()))
    assert placeholder_settings(s) == []


def test_decode_key_tolerates_quotes_and_urlsafe():
    raw = os.urandom(32)
    assert _decode_key(f'"{base64.b64encode(raw).decode()}"\n') == raw
    assert _decode_key(base64.urlsafe_b64encode(raw).decode().rstrip("=")) == raw


@pytest.mark.parametrize(
    ("value", "message"),
    [("CHANGE_ME_base64_32_bytes", "placeholder"), ("not base64 !!", "not valid base64"), ("YWJj", "32 bytes")],
)
def test_decode_key_errors_are_explicit(value, message):
    with pytest.raises(CryptoError, match=message):
        _decode_key(value)
