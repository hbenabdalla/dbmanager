import base64
import os

import pytest

from app.core.crypto import CryptoError, KeyRing


def _key() -> bytes:
    return os.urandom(32)


def test_roundtrip_and_aad_binding() -> None:
    ring = KeyRing("k1", {"k1": _key()})
    blob = ring.encrypt("s3cret!", "credential:a:monitoring")
    assert "s3cret" not in str(blob.to_dict())
    assert ring.decrypt(blob, "credential:a:monitoring") == "s3cret!"
    with pytest.raises(CryptoError):
        ring.decrypt(blob, "credential:b:monitoring")  # swapped row


def test_tamper_detected() -> None:
    ring = KeyRing("k1", {"k1": _key()})
    blob = ring.encrypt("value", "x")
    raw = bytearray(base64.b64decode(blob.ciphertext))
    raw[0] ^= 1
    tampered = blob.__class__(blob.key_id, blob.wrapped_dek, blob.nonce, base64.b64encode(bytes(raw)).decode())
    with pytest.raises(CryptoError):
        ring.decrypt(tampered, "x")


def test_rotation_rewrap() -> None:
    old, new = _key(), _key()
    ring_old = KeyRing("k1", {"k1": old})
    blob = ring_old.encrypt("pw", "aad")
    ring_new = KeyRing("k2", {"k2": new, "k1": old})
    assert ring_new.decrypt(blob, "aad") == "pw"
    rewrapped = ring_new.rewrap(blob)
    assert rewrapped.key_id == "k2"
    assert KeyRing("k2", {"k2": new}).decrypt(rewrapped, "aad") == "pw"


def test_wrong_key_fails() -> None:
    blob = KeyRing("k1", {"k1": _key()}).encrypt("pw", "aad")
    with pytest.raises(CryptoError):
        KeyRing("k1", {"k1": _key()}).decrypt(blob, "aad")


def test_sealed_json() -> None:
    ring = KeyRing("k1", {"k1": _key()})
    token = ring.seal_json({"state": "abc"}, "oidc")
    assert ring.open_json(token, "oidc") == {"state": "abc"}
    with pytest.raises(CryptoError):
        ring.open_json(token, "other")


def test_env_secret_refs_cannot_point_at_platform_configuration() -> None:
    import pytest

    from app.core.errors import ValidationFailed
    from app.services.secrets import _check_env_ref

    assert _check_env_ref("ORA_UAT_MONITOR_PASSWORD") == "ORA_UAT_MONITOR_PASSWORD"
    for bad in ("DBM_MASTER_KEY", "dbm_database_url", "PGPASSWORD", "AWS_SECRET_ACCESS_KEY", "A-B", "1X", ""):
        with pytest.raises(ValidationFailed):
            _check_env_ref(bad)
