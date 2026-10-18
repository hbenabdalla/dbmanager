"""Envelope encryption for secrets at rest.

* A random 256-bit data-encryption key (DEK) encrypts each secret with AES-256-GCM.
* The DEK is wrapped by the key-encryption key (KEK, ``DBM_MASTER_KEY``) with AES-256-GCM.
* Associated data (AAD) binds a ciphertext to its owning record so ciphertexts cannot be
  swapped between rows.
* ``key_id`` is stored alongside so the KEK can be rotated: old keys stay available for
  decryption via ``DBM_PREVIOUS_MASTER_KEYS`` while ``rewrap`` moves records to the new KEK.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings


class CryptoError(Exception):
    pass


@dataclass(frozen=True)
class EncryptedBlob:
    key_id: str
    wrapped_dek: str  # base64(nonce || ciphertext)
    nonce: str  # base64
    ciphertext: str  # base64

    def to_dict(self) -> dict[str, str]:
        return {
            "key_id": self.key_id,
            "wrapped_dek": self.wrapped_dek,
            "nonce": self.nonce,
            "ciphertext": self.ciphertext,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EncryptedBlob:
        return cls(key_id=d["key_id"], wrapped_dek=d["wrapped_dek"], nonce=d["nonce"], ciphertext=d["ciphertext"])


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode())


def _decode_key(raw: str) -> bytes:
    value = raw.strip().strip('"').strip("'").strip()
    if "CHANGE_ME" in value:
        raise CryptoError(
            "DBM_MASTER_KEY still has the placeholder value from .env.example. "
            "Generate a key (scripts/init-env.ps1 on Windows, scripts/init-env.sh elsewhere)."
        )
    try:
        # standard or URL-safe alphabet; padding may be omitted
        normalized = value.replace("-", "+").replace("_", "/")
        key = base64.b64decode(normalized + "=" * (-len(normalized) % 4), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise CryptoError(
            "DBM_MASTER_KEY is not valid base64. It must be 32 random bytes, base64 encoded "
            "(44 characters ending in '='), e.g. from scripts/init-env.ps1 or scripts/gen-master-key.sh."
        ) from exc
    if len(key) != 32:
        raise CryptoError(f"DBM_MASTER_KEY must decode to exactly 32 bytes (got {len(key)})")
    return key


class KeyRing:
    def __init__(self, current_id: str, keys: dict[str, bytes]):
        if current_id not in keys:
            raise CryptoError("current master key missing from key ring")
        self.current_id = current_id
        self._keys = keys

    @classmethod
    def from_settings(cls) -> KeyRing:
        s = get_settings()
        raw = s.master_key.get_secret_value()
        if not raw:
            raise CryptoError("DBM_MASTER_KEY is not configured")
        keys = {s.master_key_id: _decode_key(raw)}
        for entry in filter(None, (e.strip() for e in s.previous_master_keys.get_secret_value().split(","))):
            kid, _, val = entry.partition(":")
            keys[kid] = _decode_key(val)
        return cls(s.master_key_id, keys)

    def key(self, key_id: str) -> bytes:
        try:
            return self._keys[key_id]
        except KeyError as exc:
            raise CryptoError(f"unknown key id {key_id!r}") from exc

    # --- operations --------------------------------------------------------------------
    def encrypt(self, plaintext: str | bytes, aad: str) -> EncryptedBlob:
        data = plaintext.encode() if isinstance(plaintext, str) else plaintext
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ct = AESGCM(dek).encrypt(nonce, data, aad.encode())
        wrap_nonce = os.urandom(12)
        wrapped = AESGCM(self.key(self.current_id)).encrypt(wrap_nonce, dek, self.current_id.encode())
        return EncryptedBlob(self.current_id, _b64(wrap_nonce + wrapped), _b64(nonce), _b64(ct))

    def decrypt(self, blob: EncryptedBlob, aad: str) -> str:
        try:
            raw = _unb64(blob.wrapped_dek)
            dek = AESGCM(self.key(blob.key_id)).decrypt(raw[:12], raw[12:], blob.key_id.encode())
            return AESGCM(dek).decrypt(_unb64(blob.nonce), _unb64(blob.ciphertext), aad.encode()).decode()
        except CryptoError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak crypto internals
            raise CryptoError("unable to decrypt secret (wrong key or tampered data)") from exc

    def rewrap(self, blob: EncryptedBlob) -> EncryptedBlob:
        """Re-wrap the DEK with the current KEK without touching the ciphertext."""
        if blob.key_id == self.current_id:
            return blob
        raw = _unb64(blob.wrapped_dek)
        dek = AESGCM(self.key(blob.key_id)).decrypt(raw[:12], raw[12:], blob.key_id.encode())
        wrap_nonce = os.urandom(12)
        wrapped = AESGCM(self.key(self.current_id)).encrypt(wrap_nonce, dek, self.current_id.encode())
        return EncryptedBlob(self.current_id, _b64(wrap_nonce + wrapped), blob.nonce, blob.ciphertext)

    # --- small helpers for opaque encrypted tokens (e.g. OIDC state cookie) -----------
    def seal_json(self, payload: dict[str, Any], aad: str) -> str:
        blob = self.encrypt(json.dumps(payload), aad)
        return base64.urlsafe_b64encode(json.dumps(blob.to_dict()).encode()).decode()

    def open_json(self, token: str, aad: str) -> dict[str, Any]:
        try:
            blob = EncryptedBlob.from_dict(json.loads(base64.urlsafe_b64decode(token.encode())))
        except Exception as exc:  # noqa: BLE001
            raise CryptoError("malformed token") from exc
        return json.loads(self.decrypt(blob, aad))


_ring: KeyRing | None = None


def get_keyring() -> KeyRing:
    global _ring
    if _ring is None:
        _ring = KeyRing.from_settings()
    return _ring


def reset_keyring() -> None:
    global _ring
    _ring = None


def generate_master_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()
