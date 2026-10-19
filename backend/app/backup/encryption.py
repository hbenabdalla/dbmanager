"""Backup encryption with `age <https://age-encryption.org>`_ (format v1) through ``pyrage``.

DB Manager implements no cryptography of its own. Two modes:

* **recipient** — the archive is encrypted to an age X25519 public key (``age1…``). Only the
  holder of the matching identity (``AGE-SECRET-KEY-1…``) can decrypt it; DB Manager needs the
  identity only to verify contents or restore (supplied for that run, or
  ``DBM_BACKUP_IDENTITY_FILE``).
* **passphrase** — the archive is encrypted (streaming) to a fresh X25519 identity generated
  for this backup; that identity is itself encrypted with the passphrase using age's scrypt
  recipient and stored next to the archive as ``key.age``. Both files are standard age files:

  .. code-block:: shell

     age -d -o identity.txt key.age          # asks for the passphrase
     age -d -i identity.txt -o archive.tar archive.tar.age

The DB Manager master key (``DBM_MASTER_KEY``) is never written into a backup. Platform backups
contain the platform's envelope-encrypted secrets as they are stored (still encrypted with the
master key): restoring them requires the same master key (or it listed in
``DBM_PREVIOUS_MASTER_KEYS``). Keys and passphrases are never logged or stored in plain text.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MODES = ("none", "recipient", "passphrase")
ALGORITHM = {
    "recipient": "age v1 (X25519, ChaCha20-Poly1305)",
    "passphrase": "age v1 (X25519 per-backup key, wrapped with scrypt passphrase)",
}


class EncryptionError(Exception):
    pass


def _pyrage() -> Any:
    try:
        import pyrage
    except ImportError as exc:  # pragma: no cover - dependency of the backend
        raise EncryptionError("the pyrage package is not installed") from exc
    return pyrage


def parse_recipient(value: str) -> Any:
    try:
        return _pyrage().x25519.Recipient.from_str(value.strip())
    except Exception as exc:  # noqa: BLE001 - pyrage raises its own error types
        raise EncryptionError("not a valid age X25519 recipient (age1…)") from exc


def parse_identity(value: str) -> Any:
    lines = [ln.strip() for ln in value.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        raise EncryptionError("no age identity (AGE-SECRET-KEY-1…) supplied")
    try:
        return _pyrage().x25519.Identity.from_str(lines[0])
    except Exception as exc:  # noqa: BLE001
        raise EncryptionError("not a valid age X25519 identity (AGE-SECRET-KEY-1…)") from exc


def generate_identity() -> tuple[str, str]:
    """A new (identity, recipient) pair as strings — for the UI's "generate a key" helper."""
    ident = _pyrage().x25519.Identity.generate()
    return str(ident), str(ident.to_public())


def resolve_secret_ref(ref: str) -> str:
    """``env:NAME`` or ``file:/path`` (same rules as credential references)."""
    from app.services import secrets

    if ref.startswith("env:"):
        name = secrets._check_env_ref(ref[4:])
        value = os.environ.get(name)
        if not value:
            raise EncryptionError(f"environment variable {name} is not set")
        return value
    if ref.startswith("file:"):
        path = secrets._check_file_ref(ref[5:])
        try:
            return path.read_text().strip()
        except OSError as exc:
            raise EncryptionError(f"cannot read {path}: {exc.strerror}") from exc
    raise EncryptionError("a passphrase reference is env:NAME or file:/path")


def encrypt_file(
    src: Path, dst: Path, mode: str, *, recipient: str | None = None, passphrase: str | None = None
) -> bytes | None:
    """Encrypt ``src`` into ``dst`` (streaming). Returns the age-encrypted per-backup identity
    (``key.age`` contents) in passphrase mode, otherwise ``None``."""
    pr = _pyrage()
    wrapped: bytes | None = None
    if mode == "recipient":
        if not recipient:
            raise EncryptionError("recipient mode needs an age recipient")
        rcpt = parse_recipient(recipient)
    elif mode == "passphrase":
        if not passphrase or len(passphrase) < 12:
            raise EncryptionError("the passphrase must have at least 12 characters")
        ident = pr.x25519.Identity.generate()
        rcpt = ident.to_public()
        wrapped = pr.passphrase.encrypt(str(ident).encode(), passphrase)
    else:
        raise EncryptionError(f"unknown encryption mode {mode!r}")
    with open(src, "rb") as r, open(dst, "wb") as w:
        pr.encrypt_io(r, w, [rcpt])
    return wrapped


def decrypt_file(
    src: Path,
    dst: Path,
    mode: str,
    *,
    identity: str | None = None,
    passphrase: str | None = None,
    wrapped_key: bytes | None = None,
) -> None:
    pr = _pyrage()
    if mode == "recipient":
        if not identity:
            raise EncryptionError("this backup is encrypted to an age recipient: supply the matching identity")
        ident = parse_identity(identity)
    elif mode == "passphrase":
        if not passphrase or wrapped_key is None:
            raise EncryptionError("this backup is passphrase-protected: supply the passphrase")
        try:
            ident = parse_identity(pr.passphrase.decrypt(wrapped_key, passphrase).decode())
        except EncryptionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EncryptionError("wrong passphrase (or the key file is damaged)") from exc
    else:
        raise EncryptionError(f"unknown encryption mode {mode!r}")
    try:
        with open(src, "rb") as r, open(dst, "wb") as w:
            pr.decrypt_io(r, w, [ident])
    except Exception as exc:  # noqa: BLE001
        dst.unlink(missing_ok=True)
        raise EncryptionError("decryption failed: wrong key, or the archive is damaged") from exc


def recipient_fingerprint(recipient: str | None) -> str | None:
    """Recipients are public keys; shown shortened in lists."""
    if not recipient:
        return None
    r = recipient.strip()
    return f"{r[:12]}…{r[-6:]}" if len(r) > 24 else r
