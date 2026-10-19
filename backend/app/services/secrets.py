"""Credential storage and resolution.

Providers:

* ``local`` — envelope-encrypted in the application database (default).
* ``env``   — the secret is read from an environment variable named by ``secret_ref``
  (fits Kubernetes Secrets / External Secrets Operator).
* ``file``  — read from a mounted file under an allow-listed directory.
* ``vault`` — HashiCorp Vault KV v2; ``secret_ref`` = ``[mount:]path#key`` (app.services.vault).

Plaintext secrets only exist in memory for the duration of a connection attempt and are
never returned by any API.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import EncryptedBlob, get_keyring
from app.core.errors import ValidationFailed
from app.models import Credential, Server

PROVIDERS = ("local", "env", "file", "vault")


def _aad(server_id: object, purpose: str) -> str:
    return f"credential:{server_id}:{purpose}"


async def get_credential(db: AsyncSession, server_id: object, purpose: str) -> Credential | None:
    return (
        await db.execute(select(Credential).where(Credential.server_id == server_id, Credential.purpose == purpose))
    ).scalar_one_or_none()


async def set_credential(
    db: AsyncSession,
    server: Server,
    purpose: str,
    *,
    username: str,
    password: str | None,
    provider: str = "local",
    secret_ref: str | None = None,
    actor: str | None = None,
) -> Credential:
    if purpose not in ("monitoring", "admin", "readonly"):
        raise ValidationFailed("purpose must be 'monitoring', 'readonly' or 'admin'")
    if provider not in PROVIDERS:
        raise ValidationFailed(f"unknown secret provider {provider!r}")
    if provider == "vault":
        from app.services import vault

        if not vault.configured():
            raise ValidationFailed("The Vault provider needs DBM_VAULT_ADDR (see Administration → Secret providers)")
        vault.parse_ref(secret_ref or "")
    if provider in ("env", "file", "vault") and not secret_ref:
        raise ValidationFailed(f"secret_ref is required for provider '{provider}'")
    if provider == "file":
        _check_file_ref(secret_ref or "")
    if provider == "env":
        _check_env_ref(secret_ref or "")

    cred = await get_credential(db, server.id, purpose)
    if cred is None:
        cred = Credential(server_id=server.id, purpose=purpose, username=username)
        db.add(cred)
    cred.username = username
    cred.provider = provider
    cred.secret_ref = secret_ref if provider != "local" else None
    if provider == "local":
        if password is None and cred.encrypted is None:
            raise ValidationFailed("password is required")
        if password is not None:
            cred.encrypted = get_keyring().encrypt(password, _aad(server.id, purpose)).to_dict()
    else:
        cred.encrypted = None
    cred.rotated_at = datetime.now(UTC)
    cred.updated_by = actor
    await db.flush()
    return cred


#: the platform's own configuration (master key, database URL, OIDC secrets…) can never be used as
#: a database password: a credential pointing at it would send it to whatever host the server names
_RESERVED_ENV_PREFIXES = ("DBM_", "POSTGRES_", "PG", "AWS_", "AZURE_", "GOOGLE_")


def _check_env_ref(ref: str) -> str:
    import re

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", ref):
        raise ValidationFailed("secret_ref must be an environment variable name")
    if ref.upper().startswith(_RESERVED_ENV_PREFIXES):
        raise ValidationFailed(
            "environment variables of the platform itself cannot be used as database secrets",
            details={"reserved_prefixes": list(_RESERVED_ENV_PREFIXES)},
        )
    return ref


def _check_file_ref(ref: str) -> Path:
    path = Path(ref).resolve()
    allowed = [Path(d).resolve() for d in get_settings().secret_file_dirs]
    if not any(path.is_relative_to(a) for a in allowed):
        raise ValidationFailed(
            "secret file must be inside an allow-listed secrets directory",
            details={"allowed": [str(a) for a in allowed]},
        )
    return path


def resolve_password(cred: Credential) -> str | None:
    if cred.provider == "local":
        if not cred.encrypted:
            return None
        return get_keyring().decrypt(EncryptedBlob.from_dict(cred.encrypted), _aad(cred.server_id, cred.purpose))
    if cred.provider == "env":
        return os.environ.get(_check_env_ref(cred.secret_ref or ""))
    if cred.provider == "file":
        return _check_file_ref(cred.secret_ref or "").read_text().strip()
    if cred.provider == "vault":
        raise ValidationFailed("Vault secrets are read asynchronously: use resolve_password_async")
    raise ValidationFailed(f"secret provider {cred.provider!r} is not available")


async def resolve_password_async(cred: Credential) -> str | None:
    """Like :func:`resolve_password`, also for providers that need network access (Vault)."""
    if cred.provider == "vault":
        from app.services import vault

        try:
            return await vault.client.read(cred.secret_ref or "")
        except vault.VaultError as exc:
            raise ValidationFailed(
                f"Could not read the credential from Vault: {exc}", code="secret_unavailable"
            ) from exc
    return resolve_password(cred)


async def resolve_reference(provider: str, secret_ref: str | None) -> str | None:
    """Resolve a reference that is not stored yet (connection test before registration)."""
    if provider == "local":
        return None
    if not secret_ref:
        raise ValidationFailed("a secret reference is required for this secret source")
    return await resolve_password_async(Credential(provider=provider, secret_ref=secret_ref, purpose="test"))


def describe(cred: Credential | None) -> dict[str, object] | None:
    """Safe, secret-free description for API responses."""
    if cred is None:
        return None
    return {
        "purpose": cred.purpose,
        "username": cred.username,
        "provider": cred.provider,
        "secret_ref": cred.secret_ref if cred.provider in ("env", "file", "vault") else None,
        "has_secret": bool(cred.encrypted) if cred.provider == "local" else bool(cred.secret_ref),
        "rotated_at": cred.rotated_at.isoformat() if cred.rotated_at else None,
        "updated_by": cred.updated_by,
    }


async def rewrap_all(db: AsyncSession) -> int:
    """Move every local secret to the current master key (key rotation)."""
    ring = get_keyring()
    n = 0
    for cred in (await db.execute(select(Credential).where(Credential.provider == "local"))).scalars():
        if cred.encrypted and cred.encrypted.get("key_id") != ring.current_id:
            cred.encrypted = ring.rewrap(EncryptedBlob.from_dict(cred.encrypted)).to_dict()
            n += 1
    from app.models import IdentityProvider

    for p in (await db.execute(select(IdentityProvider))).scalars():
        if p.secret and p.secret.get("key_id") != ring.current_id:
            p.secret = ring.rewrap(EncryptedBlob.from_dict(p.secret)).to_dict()
            n += 1
    return n
