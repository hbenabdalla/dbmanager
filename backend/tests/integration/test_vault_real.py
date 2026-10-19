"""Vault credential provider against a real Vault dev server (sprint 0.11.5): KV v2 secrets,
token auth, AppRole auth with a policy that only allows one path, denial outside it.

Started by ``scripts/integration-test.(sh|ps1) vault``; skipped without DBM_IT_VAULT_ADDR.
"""

from __future__ import annotations

import os

import httpx
import pytest
from pydantic import SecretStr

from tests.integration import matrix

pytestmark = [pytest.mark.real_database, pytest.mark.asyncio]
ADDR = os.environ.get("DBM_IT_VAULT_ADDR")
ROOT = os.environ.get("DBM_IT_VAULT_TOKEN", "")


@pytest.mark.engine("vault")
@pytest.mark.capability("secrets", "authentication")
@pytest.mark.skipif(not ADDR, reason="DBM_IT_VAULT_ADDR not set (start: integration-test vault)")
async def test_kv2_token_and_approle(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings
    from app.services import vault

    h = {"X-Vault-Token": ROOT}
    async with httpx.AsyncClient(base_url=ADDR, headers=h, timeout=10) as c:
        health = (await c.get("/v1/sys/health")).json()
        matrix.record_version("vault", f"Vault {health.get('version')}")
        r = await c.post("/v1/secret/data/dbm/pg-prod", json={"data": {"password": "Vault-Pg-Secret-1"}})
        assert r.status_code == 200, r.text
        await c.post("/v1/secret/data/other/app", json={"data": {"password": "Not-For-DBM"}})
        await c.post("/v1/sys/auth/approle", json={"type": "approle"})  # 400 if already enabled: fine
        policy = 'path "secret/data/dbm/*" { capabilities = ["read"] }'
        assert (await c.put("/v1/sys/policies/acl/dbm-read", json={"policy": policy})).status_code == 204
        await c.post("/v1/auth/approle/role/dbm", json={"token_policies": ["dbm-read"], "token_ttl": "10m"})
        role_id = (await c.get("/v1/auth/approle/role/dbm/role-id")).json()["data"]["role_id"]
        secret_id = (await c.post("/v1/auth/approle/role/dbm/secret-id")).json()["data"]["secret_id"]

    s = get_settings()
    monkeypatch.setattr(s, "vault_addr", ADDR)
    monkeypatch.setattr(s, "vault_auth", "token")
    monkeypatch.setattr(s, "vault_token", SecretStr(ROOT))
    monkeypatch.setattr(s, "vault_cache_seconds", 0)
    vault.client.clear()
    assert await vault.client.read("dbm/pg-prod#password") == "Vault-Pg-Secret-1"
    assert (await vault.client.health())["sealed"] is False

    monkeypatch.setattr(s, "vault_auth", "approle")
    monkeypatch.setattr(s, "vault_role_id", role_id)
    monkeypatch.setattr(s, "vault_secret_id", SecretStr(secret_id))
    vault.client.clear()
    assert await vault.client.read("secret:dbm/pg-prod#password") == "Vault-Pg-Secret-1"
    with pytest.raises(vault.VaultError, match="denied"):
        await vault.client.read("other/app#password")  # outside the AppRole policy
    with pytest.raises(vault.VaultError, match="not found"):
        await vault.client.read("dbm/missing#password")
    vault.client.clear()
