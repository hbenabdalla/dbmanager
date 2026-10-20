"""Identity providers against real servers (sprint 0.11.3): OpenLDAP (service bind, user search,
password bind, group search, StartTLS, wrong password) and Keycloak (OpenID Connect
authorization-code flow with PKCE driven through Keycloak's real login form, ID token validation,
group claim, MFA requirement).

Started by ``scripts/integration-test.(sh|ps1) identity``; skipped when DBM_IT_LDAP_HOST /
DBM_IT_KEYCLOAK_URL are not set. Fixtures: tests/integration/identity/.
"""

from __future__ import annotations

import os
import re
import uuid
from html import unescape
from types import SimpleNamespace
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
import pytest

from tests.integration import matrix

pytestmark = [pytest.mark.real_database, pytest.mark.asyncio]

LDAP_HOST = os.environ.get("DBM_IT_LDAP_HOST")
LDAP_PORT = int(os.environ.get("DBM_IT_LDAP_PORT", "389"))
KEYCLOAK = os.environ.get("DBM_IT_KEYCLOAK_URL")


def _provider(kind: str, config: dict, secret: str | None) -> SimpleNamespace:  # type: ignore[type-arg]
    from app.core.crypto import get_keyring

    pid = uuid.uuid4()
    sealed = get_keyring().encrypt(secret, f"idp:{pid}").to_dict() if secret else None
    return SimpleNamespace(id=pid, kind=kind, name=f"it-{kind}", enabled=True, config=config, secret=sealed)


# ---- LDAP ---------------------------------------------------------------------------------------------
LDAP_CFG = {
    "user_base_dn": "ou=people,dc=example,dc=org",
    "user_filter": "(&(objectClass=inetOrgPerson)(uid={username}))",
    "bind_dn": "uid=svc-dbm,ou=people,dc=example,dc=org",
    "group_search_base": "ou=groups,dc=example,dc=org",
    "group_filter": "(&(objectClass=groupOfNames)(member={user_dn}))",
    "attr_username": "uid",
    "attr_email": "mail",
    "attr_display_name": "displayName",
    "allow_insecure": True,
    "timeout": 8,
}


def _read_text(path: str) -> str:
    from pathlib import Path

    return Path(path).read_text()


@pytest.mark.engine("openldap")
@pytest.mark.capability("authentication", "group_mapping")
@pytest.mark.skipif(not LDAP_HOST, reason="DBM_IT_LDAP_HOST not set (start: integration-test identity)")
async def test_ldap_bind_search_and_groups() -> None:
    from app.services import ldap_auth

    cfg = {**LDAP_CFG, "server_uri": f"ldap://{LDAP_HOST}:{LDAP_PORT}"}
    p = _provider("ldap", cfg, "It-Bind-Passw0rd")
    probe = await ldap_auth.test_provider(p)
    assert probe["bind"] is True, probe
    matrix.record_version("openldap", os.environ.get("DBM_IT_LDAP_LABEL", "OpenLDAP (osixia/openldap 1.5.0)"))
    entry = ldap_auth._ldap_lookup_and_bind(cfg, "It-Bind-Passw0rd", "alice", "It-Alice-Passw0rd")
    assert entry is not None and entry.dn == "uid=alice,ou=people,dc=example,dc=org"
    assert entry.email == "alice@example.org" and entry.display_name == "Alice Admin"
    # found through memberOf and the group search, each group is listed once
    assert sorted(g.split(",")[0] for g in entry.groups) == ["cn=dbadmins", "cn=developers"]
    bob = ldap_auth._ldap_lookup_and_bind(cfg, "It-Bind-Passw0rd", "bob", "It-Bob-Passw0rd")
    assert bob is not None and {g.split(",")[0] for g in bob.groups} == {"cn=developers"}
    # wrong user password, unknown user, LDAP injection attempt
    assert ldap_auth._ldap_lookup_and_bind(cfg, "It-Bind-Passw0rd", "alice", "wrong") is None
    assert ldap_auth._ldap_lookup_and_bind(cfg, "It-Bind-Passw0rd", "nobody", "x") is None
    assert ldap_auth._ldap_lookup_and_bind(cfg, "It-Bind-Passw0rd", "*)(uid=*", "x") is None
    # a wrong service password fails loudly
    with pytest.raises(RuntimeError, match="service bind failed"):
        ldap_auth._ldap_lookup_and_bind(cfg, "wrong", "alice", "It-Alice-Passw0rd")


@pytest.mark.engine("openldap")
@pytest.mark.capability("authentication")
@pytest.mark.skipif(not LDAP_HOST, reason="DBM_IT_LDAP_HOST not set (start: integration-test identity)")
async def test_ldap_starttls() -> None:
    from app.services import ldap_auth

    # the container's certificate is self-signed: chain verification is disabled for the lab only
    cfg = {**LDAP_CFG, "server_uri": f"ldap://{LDAP_HOST}:{LDAP_PORT}", "start_tls": True, "verify_cert": False}
    entry = ldap_auth._ldap_lookup_and_bind(cfg, "It-Bind-Passw0rd", "alice", "It-Alice-Passw0rd")
    assert entry is not None and entry.username == "alice"
    # with verification on, the self-signed certificate is refused
    strict = {**cfg, "verify_cert": True}
    with pytest.raises(Exception):  # noqa: B017 - ldap3 raises its own socket/TLS exception types
        ldap_auth._ldap_lookup_and_bind(strict, "It-Bind-Passw0rd", "alice", "It-Alice-Passw0rd")
    # with the directory's CA (DBM_IT_LDAP_TLS_CA, PEM) the verified StartTLS bind succeeds
    ca_path = os.environ.get("DBM_IT_LDAP_TLS_CA")
    if ca_path:
        verified = {**strict, "ca_pem": _read_text(ca_path)}
        entry = ldap_auth._ldap_lookup_and_bind(verified, "It-Bind-Passw0rd", "alice", "It-Alice-Passw0rd")
        assert entry is not None and entry.username == "alice"


# ---- OpenID Connect -------------------------------------------------------------------------------------
async def _keycloak_code(authorize_url: str, username: str, password: str) -> tuple[str, str]:
    """Drive Keycloak's login page like a browser; returns (code, state) from the redirect."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as c:
        page = await c.get(authorize_url)
        assert page.status_code == 200, page.text[:500]
        # the login form posts to .../login-actions/authenticate?session_code=… (both login themes)
        m = re.search(r'action="([^"]*login-actions/authenticate[^"]*)"', page.text)
        assert m, "Keycloak login form not found"
        action = urljoin(str(page.url), unescape(m.group(1)))
        r = await c.post(
            action, data={"username": username, "password": password, "credentialId": ""}, follow_redirects=False
        )
        assert r.status_code in (302, 303), r.text[:500]
        location = r.headers["location"]
    q = parse_qs(urlsplit(location).query)
    assert location.startswith("http://it-runner/"), location
    return q["code"][0], q["state"][0]


@pytest.mark.engine("keycloak")
@pytest.mark.capability("authentication", "group_mapping", "mfa")
@pytest.mark.skipif(not KEYCLOAK, reason="DBM_IT_KEYCLOAK_URL not set (start: integration-test identity)")
async def test_oidc_code_flow_with_pkce_against_keycloak(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.errors import NotAuthenticated
    from app.services import oidc

    cfg = {
        "issuer": f"{KEYCLOAK}/realms/dbm",
        "client_id": "dbmanager",
        "redirect_uri": "http://it-runner/api/v1/auth/oidc/callback",
        "scopes": "openid profile email",
        "groups_claim": "groups",
    }
    p = _provider("oidc", cfg, "it-dbmanager-client-secret")
    url, sealed = await oidc.start(p)
    assert "code_challenge_method=S256" in url
    code, state = await _keycloak_code(url, "alice", "It-Alice-Passw0rd")
    claims = await oidc.exchange_and_validate(p, code, state, sealed)
    assert claims["preferred_username"] == "alice" and claims["email"] == "alice@example.org"
    assert set(oidc.groups_of(cfg, claims)) == {"dbadmins", "developers"}
    async with httpx.AsyncClient() as c:
        server = (await c.get(f"{KEYCLOAK}/realms/dbm/.well-known/openid-configuration")).json()
    matrix.record_version("keycloak", f"Keycloak 26 ({server.get('issuer')})")

    # replaying the code or tampering with state is refused
    with pytest.raises(NotAuthenticated):
        await oidc.exchange_and_validate(p, code, state, sealed)
    url2, sealed2 = await oidc.start(p)
    code2, _ = await _keycloak_code(url2, "bob", "It-Bob-Passw0rd")
    with pytest.raises(NotAuthenticated, match="State mismatch"):
        await oidc.exchange_and_validate(p, code2, "forged-state", sealed2)

    # require_mfa: a password-only Keycloak sign-in carries no MFA evidence (amr/acr) → refused
    strict = _provider("oidc", {**cfg, "require_mfa": True}, "it-dbmanager-client-secret")
    url3, sealed3 = await oidc.start(strict)
    code3, state3 = await _keycloak_code(url3, "bob", "It-Bob-Passw0rd")
    with pytest.raises(NotAuthenticated, match="multi-factor"):
        await oidc.exchange_and_validate(strict, code3, state3, sealed3)

    # a wrong client secret makes the token exchange fail
    wrong = _provider("oidc", cfg, "not-the-secret")
    url4, sealed4 = await oidc.start(wrong)
    code4, state4 = await _keycloak_code(url4, "alice", "It-Alice-Passw0rd")
    with pytest.raises(NotAuthenticated, match="Token exchange"):
        await oidc.exchange_and_validate(wrong, code4, state4, sealed4)
