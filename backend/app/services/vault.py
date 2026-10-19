"""HashiCorp Vault KV version 2 as a credential provider (sprint 0.11.5).

A credential with ``provider = "vault"`` stores only a reference, ``[mount:]path#key`` — e.g.
``databases/pg-prod#password`` (mount ``DBM_VAULT_KV_MOUNT``, default ``secret``) or
``kv-dba:oracle/erp#password``. The password is read from Vault when a connection is opened,
kept in memory for ``DBM_VAULT_CACHE_SECONDS`` at most, and never stored or returned.

Authentication (``DBM_VAULT_AUTH``):

* ``token`` — ``DBM_VAULT_TOKEN`` or a file with the token (``DBM_VAULT_TOKEN_FILE``, e.g. written
  by Vault Agent);
* ``approle`` — ``DBM_VAULT_ROLE_ID`` + ``DBM_VAULT_SECRET_ID`` (or ``…_FILE``);
* ``kubernetes`` — the pod's service-account JWT and ``DBM_VAULT_KUBERNETES_ROLE``.

Login tokens are renewed by logging in again shortly before they expire. Vault Enterprise
namespaces are sent as ``X-Vault-Namespace``; ``DBM_VAULT_CA_FILE`` verifies a private CA.
"""

from __future__ import annotations

import re
import ssl
import time
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import ValidationFailed

REF = re.compile(
    r"^(?:(?P<mount>[A-Za-z0-9_\-/]{1,64}):)?(?P<path>[A-Za-z0-9_\-./]{1,256})#(?P<key>[A-Za-z0-9_\-.]{1,128})$"
)


class VaultError(Exception):
    pass


def parse_ref(ref: str) -> tuple[str, str, str]:
    m = REF.match(ref or "")
    if not m or ".." in m["path"] or m["path"].startswith("/"):
        raise ValidationFailed(
            "Vault references look like 'path/to/secret#key' or 'mount:path/to/secret#key'",
            details={"example": "databases/pg-prod#password"},
        )
    return (m["mount"] or get_settings().vault_kv_mount or "secret").strip("/"), m["path"].strip("/"), m["key"]


def configured() -> bool:
    return bool(get_settings().vault_addr)


def _read_file(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        raise VaultError(f"cannot read {path}: {exc.strerror}") from exc


class VaultClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expires = 0.0
        self._cache: dict[str, tuple[float, str]] = {}

    # -- plumbing -----------------------------------------------------------------------------
    def _client(self) -> httpx.AsyncClient:
        s = get_settings()
        verify: ssl.SSLContext | bool = True
        if s.vault_ca_file:
            verify = ssl.create_default_context(cafile=s.vault_ca_file)
        headers = {"X-Vault-Request": "true"}
        if s.vault_namespace:
            headers["X-Vault-Namespace"] = s.vault_namespace
        return httpx.AsyncClient(
            base_url=s.vault_addr.rstrip("/"), timeout=s.vault_timeout_seconds, verify=verify, headers=headers
        )

    async def _login(self, c: httpx.AsyncClient) -> str:
        s = get_settings()
        if s.vault_auth == "token":
            token = s.vault_token.get_secret_value() or (_read_file(s.vault_token_file) if s.vault_token_file else "")
            if not token:
                raise VaultError("no Vault token configured (DBM_VAULT_TOKEN or DBM_VAULT_TOKEN_FILE)")
            return token
        if self._token and time.monotonic() < self._token_expires:
            return self._token
        if s.vault_auth == "approle":
            secret_id = s.vault_secret_id.get_secret_value() or (
                _read_file(s.vault_secret_id_file) if s.vault_secret_id_file else ""
            )
            body: dict[str, Any] = {"role_id": s.vault_role_id, "secret_id": secret_id}
            mount = s.vault_auth_mount or "approle"
        else:
            body = {"role": s.vault_kubernetes_role, "jwt": _read_file(s.vault_kubernetes_jwt_file)}
            mount = s.vault_auth_mount or "kubernetes"
        r = await c.post(f"/v1/auth/{mount.strip('/')}/login", json=body)
        if r.status_code != 200:
            raise VaultError(f"Vault {s.vault_auth} login failed (HTTP {r.status_code})")
        auth = r.json().get("auth") or {}
        self._token = auth.get("client_token")
        if not self._token:
            raise VaultError("Vault login returned no token")
        ttl = int(auth.get("lease_duration") or 300)
        self._token_expires = time.monotonic() + max(ttl - 30, ttl / 2)
        return self._token

    # -- API ----------------------------------------------------------------------------------
    async def read(self, ref: str) -> str:
        mount, path, key = parse_ref(ref)
        s = get_settings()
        cached = self._cache.get(ref)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        if not s.vault_addr:
            raise VaultError("Vault is not configured (DBM_VAULT_ADDR)")
        async with self._client() as c:
            token = await self._login(c)
            try:
                r = await c.get(f"/v1/{mount}/data/{path}", headers={"X-Vault-Token": token})
            except httpx.HTTPError as exc:
                raise VaultError(f"Vault is not reachable: {type(exc).__name__}") from exc
            if r.status_code == 403 and s.vault_auth != "token":
                self._token = None  # expired or revoked: log in once more
                token = await self._login(c)
                r = await c.get(f"/v1/{mount}/data/{path}", headers={"X-Vault-Token": token})
        if r.status_code == 404:
            raise VaultError(f"secret {mount}/{path} not found in Vault")
        if r.status_code == 403:
            raise VaultError(f"Vault denied access to {mount}/{path}")
        if r.status_code != 200:
            raise VaultError(f"Vault answered HTTP {r.status_code} for {mount}/{path}")
        data = ((r.json().get("data") or {}).get("data")) or {}
        if key not in data:
            raise VaultError(f"secret {mount}/{path} has no key {key!r}")
        value = str(data[key])
        if s.vault_cache_seconds:
            self._cache[ref] = (time.monotonic() + s.vault_cache_seconds, value)
        return value

    async def health(self) -> dict[str, Any]:
        s = get_settings()
        if not s.vault_addr:
            return {"configured": False}
        try:
            async with self._client() as c:
                r = await c.get("/v1/sys/health", params={"standbyok": "true", "perfstandbyok": "true"})
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            return {
                "configured": True,
                "reachable": True,
                "initialized": body.get("initialized"),
                "sealed": body.get("sealed"),
                "version": body.get("version"),
                "auth": s.vault_auth,
            }
        except (httpx.HTTPError, ValueError) as exc:
            return {"configured": True, "reachable": False, "error": type(exc).__name__, "auth": s.vault_auth}

    def clear(self) -> None:
        self._token, self._token_expires, self._cache = None, 0.0, {}


client = VaultClient()
