"""OpenID Connect authorization-code flow with PKCE.

Works with any compliant provider (Keycloak, Microsoft Entra ID, Okta, Auth0, ...).
State, nonce and the PKCE verifier are sealed (AES-GCM) into a short-lived HttpOnly
cookie, so the flow works across replicas without server-side state.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt as jose_jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import CryptoError, get_keyring
from app.core.errors import NotAuthenticated, ValidationFailed
from app.models import IdentityProvider, User
from app.services.identity_sync import provision_external_user
from app.services.ldap_auth import provider_secret

FLOW_COOKIE = "dbm_oidc"
#: RFC 8176 authentication method references that prove a second factor
MFA_AMR_VALUES = ("mfa", "otp", "hwk", "swk", "sc", "fpt", "face", "iris", "retina", "vbm", "pin")
_metadata_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def redirect_uri(provider: IdentityProvider) -> str:
    return provider.config.get("redirect_uri") or (
        f"{get_settings().public_base_url.rstrip('/')}/api/v1/auth/oidc/{provider.id}/callback"
    )


async def _metadata(issuer: str) -> dict[str, Any]:
    cached = _metadata_cache.get(issuer)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        meta = resp.json()
    _metadata_cache[issuer] = (time.time(), meta)
    return meta


async def start(provider: IdentityProvider) -> tuple[str, str]:
    cfg = provider.config
    meta = await _metadata(cfg["issuer"])
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri(provider),
        "scope": cfg.get("scopes", "openid profile email"),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if cfg.get("prompt"):
        params["prompt"] = cfg["prompt"]
    if cfg.get("acr_values"):
        params["acr_values"] = cfg["acr_values"]
    sealed = get_keyring().seal_json(
        {"state": state, "nonce": nonce, "verifier": verifier, "pid": str(provider.id), "ts": time.time()}, "oidc-flow"
    )
    return f"{meta['authorization_endpoint']}?{urlencode(params)}", sealed


def _claim(claims: dict[str, Any], path: str) -> Any:
    cur: Any = claims
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


async def callback(db: AsyncSession, provider: IdentityProvider, code: str, state: str, sealed: str | None) -> User:
    if not sealed:
        raise NotAuthenticated("Login session expired; start sign-in again")
    try:
        flow = get_keyring().open_json(sealed, "oidc-flow")
    except CryptoError as exc:
        raise NotAuthenticated("Invalid login session") from exc
    if flow.get("pid") != str(provider.id) or not secrets.compare_digest(flow.get("state", ""), state):
        raise NotAuthenticated("State mismatch")
    if time.time() - float(flow.get("ts", 0)) > 600:
        raise NotAuthenticated("Login session expired; start sign-in again")

    cfg = provider.config
    meta = await _metadata(cfg["issuer"])
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(provider),
        "client_id": cfg["client_id"],
        "code_verifier": flow["verifier"],
    }
    secret = provider_secret(provider)
    auth = None
    if secret:
        if cfg.get("token_auth_method", "client_secret_basic") == "client_secret_post":
            data["client_secret"] = secret
        else:
            auth = httpx.BasicAuth(cfg["client_id"], secret)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(meta["token_endpoint"], data=data, auth=auth)
        if resp.status_code != 200:
            raise NotAuthenticated("Token exchange with the identity provider failed")
        tokens = resp.json()
        jwks = (await client.get(meta["jwks_uri"])).json()

    id_token = tokens.get("id_token")
    if not id_token:
        raise NotAuthenticated("Identity provider returned no ID token")
    try:
        token = jose_jwt.decode(
            id_token, KeySet.import_key_set(jwks), algorithms=["RS256", "RS384", "RS512", "PS256", "ES256", "ES384"]
        )
        registry = jose_jwt.JWTClaimsRegistry(
            leeway=60,
            iss={"essential": True, "value": meta.get("issuer", cfg["issuer"])},
            aud={"essential": True, "value": cfg["client_id"]},
            nonce={"essential": True, "value": flow["nonce"]},
            exp={"essential": True},
        )
        registry.validate(token.claims)
    except (JoseError, ValueError) as exc:
        raise NotAuthenticated("The identity provider's ID token could not be validated") from exc
    claims = token.claims
    check_mfa(cfg, claims)

    username = _claim(claims, cfg.get("username_claim", "preferred_username")) or claims.get("email") or claims["sub"]
    email = claims.get("email")
    allowed = cfg.get("allowed_email_domains") or []
    if allowed and (not email or email.split("@")[-1].lower() not in [d.lower() for d in allowed]):
        raise NotAuthenticated("Your account is not permitted to sign in")
    groups = _claim(claims, cfg.get("groups_claim", "groups")) or []
    if isinstance(groups, str):
        groups = [groups]
    user = await provision_external_user(
        db,
        provider,
        subject=str(claims["sub"]),
        username=str(username),
        email=email,
        display_name=claims.get("name"),
        external_groups=[str(g) for g in groups],
    )
    if user is None:
        raise NotAuthenticated("Your account is not permitted to sign in")
    return user


def check_mfa(cfg: dict[str, Any], claims: dict[str, Any]) -> None:
    """With ``require_mfa`` the ID token must prove a second factor: an ``amr`` value from
    ``mfa_amr_values`` (default: RFC 8176 multi-factor methods) or an ``acr`` listed in
    ``required_acr`` (e.g. the provider's MFA level of assurance)."""
    if not cfg.get("require_mfa"):
        return
    amr = claims.get("amr") or []
    if isinstance(amr, str):
        amr = [amr]
    accepted = {str(v).lower() for v in (cfg.get("mfa_amr_values") or MFA_AMR_VALUES)}
    acr_ok = str(claims.get("acr") or "") in {str(v) for v in cfg.get("required_acr") or []}
    if not (acr_ok or accepted & {str(v).lower() for v in amr}):
        raise NotAuthenticated("Your identity provider did not confirm multi-factor authentication for this sign-in")


def validate_config(kind: str, cfg: dict[str, Any]) -> None:
    required = {"oidc": ["issuer", "client_id"], "ldap": ["server_uri", "user_base_dn"], "saml": []}[kind]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise ValidationFailed("Identity provider configuration is incomplete", details={"missing": missing})
    if (
        kind == "ldap"
        and not (cfg["server_uri"].startswith("ldaps://") or cfg.get("start_tls"))
        and not cfg.get("allow_insecure")
    ):
        raise ValidationFailed(
            "LDAP must use ldaps:// or StartTLS (set allow_insecure only for lab environments)",
            details={"server_uri": cfg["server_uri"]},
        )
