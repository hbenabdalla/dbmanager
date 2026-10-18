"""Identity-provider setup wizards: staged tests of unsaved (draft) configurations and OIDC presets."""

from __future__ import annotations

import asyncio
import socket
import ssl
import time
import uuid
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentPrincipal
from app.core.errors import NotFound, ValidationFailed
from app.models import IdentityProvider
from app.services import audit

router = APIRouter(tags=["platform"])

PRESETS: list[dict[str, Any]] = [
    {
        "key": "keycloak",
        "label": "Keycloak",
        "issuer_template": "https://keycloak.example.com/realms/{realm}",
        "issuer_help": "Realm URL: https://<host>/realms/<realm> (Keycloak 17+; older releases add /auth).",
        "scopes": ["openid", "profile", "email"],
        "username_claim": "preferred_username",
        "email_claim": "email",
        "groups_claim": "groups",
        "notes": "Add a 'groups' client scope with the Group Membership mapper (full group path off) to send groups.",
    },
    {
        "key": "entra",
        "label": "Microsoft Entra ID",
        "issuer_template": "https://login.microsoftonline.com/{tenant_id}/v2.0",
        "issuer_help": "Use the tenant ID (GUID) of your directory; the v2.0 endpoint is required.",
        "scopes": ["openid", "profile", "email"],
        "username_claim": "preferred_username",
        "email_claim": "email",
        "groups_claim": "groups",
        "notes": "Enable 'groups' in Token configuration (emits group object IDs). Users in more than 200 groups "
        "receive an overage claim instead; map by object ID.",
    },
    {
        "key": "okta",
        "label": "Okta",
        "issuer_template": "https://{your-org}.okta.com/oauth2/default",
        "issuer_help": "Custom authorization server URL (default: /oauth2/default); the org server has no groups claim.",
        "scopes": ["openid", "profile", "email", "groups"],
        "username_claim": "preferred_username",
        "email_claim": "email",
        "groups_claim": "groups",
        "notes": "Add a 'groups' claim to the authorization server (filter e.g. Matches regex .*).",
    },
    {
        "key": "generic",
        "label": "Generic OpenID Connect",
        "issuer_template": "https://idp.example.com",
        "issuer_help": "Issuer URL; /.well-known/openid-configuration must be reachable from the backend.",
        "scopes": ["openid", "profile", "email"],
        "username_claim": "preferred_username",
        "email_claim": "email",
        "groups_claim": "groups",
        "notes": "Authorization code flow with PKCE; the client may be confidential (client secret) or public.",
    },
]


class DraftTest(BaseModel):
    kind: Literal["ldap", "oidc"]
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(None, max_length=4096, description="write-only")
    provider_id: uuid.UUID | None = None
    test_username: str | None = Field(None, max_length=256)
    test_password: str | None = Field(None, max_length=4096, description="write-only")


def _stage(stage: str, status: str, message: str, t0: float | None = None, detail: Any = None) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": status,
        "message": message,
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1) if t0 is not None else None,
        "detail": detail,
    }


class _Run:
    def __init__(self, order: list[str]) -> None:
        self.order, self.stages = order, []  # type: list[str], list[dict[str, Any]]

    def add(self, s: dict[str, Any]) -> None:
        self.stages.append(s)

    def skip_rest(self, reason: str) -> None:
        done = {s["stage"] for s in self.stages}
        self.stages += [_stage(s, "skipped", reason) for s in self.order if s not in done]

    @property
    def ok(self) -> bool:
        return all(s["status"] in ("ok", "warn", "skipped") for s in self.stages) and any(
            s["status"] in ("ok", "warn") for s in self.stages
        )


# ---- LDAP ---------------------------------------------------------------------------------
LDAP_STAGES = ["dns", "tcp", "tls", "bind", "user_search", "group_lookup", "user_bind"]


def _tls_context(cfg: dict[str, Any]) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cadata=cfg.get("ca_pem") or None)
    if not cfg.get("verify_cert", True):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _ldap_test(cfg: dict[str, Any], secret: str | None, user: str | None, user_pw: str | None) -> _Run:
    run = _Run(LDAP_STAGES)
    uri = str(cfg.get("server_uri") or "")
    u = urlparse(uri)
    if u.scheme not in ("ldap", "ldaps") or not u.hostname:
        run.add(_stage("dns", "fail", "server_uri must look like ldaps://host:636 or ldap://host:389"))
        run.skip_rest("skipped: invalid server URI")
        return run
    host, port = u.hostname, u.port or (636 if u.scheme == "ldaps" else 389)
    timeout = float(cfg.get("timeout") or 8)
    t0 = time.perf_counter()
    try:
        infos = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout
        )
        addrs = sorted({str(i[4][0]) for i in infos})
        run.add(_stage("dns", "ok", ", ".join(addrs[:4]), t0, {"addresses": addrs}))
    except (TimeoutError, OSError) as exc:
        run.add(_stage("dns", "fail", f"cannot resolve {host}: {exc}", t0))
        run.skip_rest("skipped: dns failed")
        return run
    t0 = time.perf_counter()
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(addrs[0], port), timeout)
        w.close()
        run.add(_stage("tcp", "ok", f"{host}:{port} is reachable", t0))
    except (TimeoutError, OSError) as exc:
        run.add(_stage("tcp", "fail", f"{host}:{port} unreachable: {getattr(exc, 'strerror', None) or exc}", t0))
        run.skip_rest("skipped: tcp failed")
        return run
    t0 = time.perf_counter()
    if u.scheme == "ldaps":
        try:
            _r, w = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=_tls_context(cfg), server_hostname=host), timeout
            )
            ssl_obj = w.get_extra_info("ssl_object")
            ver = ssl_obj.version() if ssl_obj else None
            w.close()
            verified = cfg.get("verify_cert", True)
            run.add(
                _stage(
                    "tls",
                    "ok" if verified else "warn",
                    f"{ver or 'TLS'} handshake" + ("" if verified else " (certificate NOT verified)"),
                    t0,
                )
            )
        except (ssl.SSLError, TimeoutError, OSError) as exc:
            run.add(_stage("tls", "fail", f"TLS handshake failed: {exc}", t0))
            run.skip_rest("skipped: tls failed")
            return run
    elif not cfg.get("start_tls"):
        run.add(
            _stage(
                "tls",
                "warn" if cfg.get("allow_insecure") else "fail",
                "plain LDAP: credentials would travel unencrypted (use ldaps:// or StartTLS)",
                t0,
            )
        )
        if not cfg.get("allow_insecure"):
            run.skip_rest("skipped: insecure transport not allowed")
            return run

    def _sync() -> list[dict[str, Any]]:
        from ldap3 import SUBTREE, Connection
        from ldap3.utils.conv import escape_filter_chars

        from app.services.ldap_auth import _server

        out: list[dict[str, Any]] = []
        server = _server(cfg)
        conn = Connection(
            server,
            user=cfg.get("bind_dn") or None,
            password=secret or None,
            receive_timeout=int(timeout),
            raise_exceptions=False,
        )
        t = time.perf_counter()
        if cfg.get("start_tls") and u.scheme == "ldap":
            try:
                conn.open()
                conn.start_tls()
                out.append(_stage("tls", "ok", "StartTLS negotiated", t))
            except Exception as exc:  # noqa: BLE001
                out.append(_stage("tls", "fail", f"StartTLS failed: {exc}", t))
                return out
        t = time.perf_counter()
        if not conn.bind():
            desc = (conn.result or {}).get("description") or "bind failed"
            out.append(_stage("bind", "fail", f"service account bind failed: {desc}", t))
            return out
        who = cfg.get("bind_dn") or "anonymous"
        out.append(_stage("bind", "ok" if cfg.get("bind_dn") else "warn", f"bound as {who}", t))
        try:
            if not user:
                out.append(_stage("user_search", "skipped", "enter a test user name to check the user search"))
                return out
            t = time.perf_counter()
            flt = cfg.get("user_filter", "(&(objectClass=person)(uid={username}))").replace(
                "{username}", escape_filter_chars(user)
            )
            group_attr = cfg.get("group_attribute", "memberOf")
            conn.search(cfg.get("user_base_dn") or "", flt, SUBTREE, attributes=[group_attr], size_limit=2)
            if len(conn.entries) != 1:
                out.append(
                    _stage(
                        "user_search",
                        "fail",
                        f"filter {flt} returned {len(conn.entries)} entries under {cfg.get('user_base_dn')} (expected 1)",
                        t,
                    )
                )
                return out
            e = conn.entries[0]
            dn = e.entry_dn
            out.append(_stage("user_search", "ok", f"found {dn}", t, {"dn": dn}))
            t = time.perf_counter()
            groups = [str(g) for g in (e[group_attr].values if group_attr in e else [])]
            if cfg.get("group_search_base"):
                gflt = cfg.get("group_filter", "(member={user_dn})").replace("{user_dn}", escape_filter_chars(dn))
                conn.search(cfg["group_search_base"], gflt, SUBTREE, attributes=["cn"])
                groups += [g.entry_dn for g in conn.entries]
            out.append(
                _stage(
                    "group_lookup",
                    "ok" if groups else "warn",
                    f"{len(groups)} group(s)" if groups else "no groups found: group mappings will not apply",
                    t,
                    {"groups": groups[:50]},
                )
            )
        finally:
            conn.unbind()
        if not user_pw:
            out.append(_stage("user_bind", "skipped", "enter the test user's password to check the login bind"))
            return out
        t = time.perf_counter()
        uc = Connection(server, user=dn, password=user_pw, raise_exceptions=False, receive_timeout=int(timeout))
        if cfg.get("start_tls") and u.scheme == "ldap":
            uc.open()
            uc.start_tls()
        ok = uc.bind()
        uc.unbind()
        out.append(
            _stage("user_bind", "ok" if ok else "fail", "user password accepted" if ok else "user bind failed", t)
        )
        return out

    try:
        for s in await asyncio.to_thread(_sync):
            run.add(s)
    except Exception as exc:  # noqa: BLE001
        run.add(_stage("bind", "fail", f"{type(exc).__name__}: {exc}"))
    if any(s["status"] == "fail" for s in run.stages):
        run.skip_rest("skipped: an earlier stage failed")
    else:
        run.skip_rest("skipped")
    return run


# ---- OIDC ---------------------------------------------------------------------------------
OIDC_STAGES = ["discovery", "issuer_match", "jwks", "endpoints", "redirect_uri"]


async def _oidc_test(cfg: dict[str, Any], provider_id: uuid.UUID | None) -> _Run:
    from app.core.config import get_settings

    run = _Run(OIDC_STAGES)
    issuer = str(cfg.get("issuer") or "").strip()
    if not issuer.startswith(("https://", "http://")):
        run.add(_stage("discovery", "fail", "issuer must be an https:// URL"))
        run.skip_rest("skipped: invalid issuer")
        return run
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=float(cfg.get("timeout") or 10)) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            meta = resp.json()
            if not isinstance(meta, dict):
                raise ValueError("discovery document is not a JSON object")
            run.add(_stage("discovery", "ok", f"fetched {url}", t0))
            t0 = time.perf_counter()
            got = str(meta.get("issuer") or "")
            if got.rstrip("/") == issuer.rstrip("/"):
                run.add(_stage("issuer_match", "ok", f"issuer {got}", t0))
            else:
                run.add(_stage("issuer_match", "fail", f"discovery says issuer {got!r}, configured {issuer!r}", t0))
            t0 = time.perf_counter()
            jwks_uri = meta.get("jwks_uri")
            if not jwks_uri:
                run.add(_stage("jwks", "fail", "no jwks_uri in the discovery document", t0))
            else:
                try:
                    jr = await client.get(str(jwks_uri))
                    jr.raise_for_status()
                    keys = (jr.json() or {}).get("keys") or []
                    run.add(
                        _stage(
                            "jwks",
                            "ok" if keys else "fail",
                            f"{len(keys)} signing key(s)",
                            t0,
                            {"algorithms": sorted({k.get("alg") for k in keys if k.get("alg")})},
                        )
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    run.add(_stage("jwks", "fail", f"cannot fetch {jwks_uri}: {exc}", t0))
    except (httpx.HTTPError, ValueError) as exc:
        run.add(_stage("discovery", "fail", f"cannot fetch {url}: {type(exc).__name__}: {exc}", t0))
        run.skip_rest("skipped: discovery failed")
        return run
    t0 = time.perf_counter()
    missing = [k for k in ("authorization_endpoint", "token_endpoint") if not meta.get(k)]
    insecure = [k for k in ("authorization_endpoint", "token_endpoint") if str(meta.get(k, "")).startswith("http://")]
    pkce = "S256" in (meta.get("code_challenge_methods_supported") or ["S256"])
    if missing:
        run.add(_stage("endpoints", "fail", f"missing {', '.join(missing)}", t0))
    elif insecure or not pkce:
        run.add(
            _stage(
                "endpoints",
                "warn",
                ("plain http endpoints: " + ", ".join(insecure)) if insecure else "PKCE S256 not advertised",
                t0,
            )
        )
    else:
        run.add(_stage("endpoints", "ok", "authorization and token endpoints (PKCE S256)", t0))
    base = get_settings().public_base_url.rstrip("/")
    redirect = cfg.get("redirect_uri") or f"{base}/api/v1/auth/oidc/{provider_id or '<provider-id>'}/callback"
    status = "ok" if (cfg.get("redirect_uri") or provider_id) else "warn"
    msg = f"register {redirect} as an allowed redirect URI" + (
        "" if status == "ok" else " (the provider id is assigned when you save)"
    )
    if str(redirect).startswith("http://") and "localhost" not in str(redirect):
        status, msg = "warn", msg + "; use https in production"
    run.add(_stage("redirect_uri", status, msg, t0, {"redirect_uri": redirect}))
    return run


@router.get("/identity-providers/presets", summary="OIDC presets for Keycloak, Entra ID, Okta and generic providers")
async def idp_presets(p: CurrentPrincipal) -> list[dict[str, Any]]:
    p.authz.require("idp.manage")
    return PRESETS


@router.post("/identity-providers/test-draft", summary="Staged test of an unsaved LDAP / OIDC configuration")
async def test_draft(body: DraftTest, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("idp.manage")
    from app.services.ldap_auth import provider_secret
    from app.services.oidc import validate_config

    secret = body.secret
    if body.provider_id is not None:
        stored = await db.get(IdentityProvider, body.provider_id)
        if stored is None:
            raise NotFound("Provider not found")
        if secret is None:
            secret = provider_secret(stored)
    cfg = {k: v for k, v in body.config.items() if k not in ("bind_password", "client_secret", "password", "secret")}
    try:
        validate_config(body.kind, cfg)
        problem = None
    except ValidationFailed as exc:
        problem = exc.message + (f": {', '.join(exc.details.get('missing', []))}" if exc.details else "")
    if problem and not (body.kind == "ldap" and "ldaps://" in problem):
        order = LDAP_STAGES if body.kind == "ldap" else OIDC_STAGES
        run = _Run(order)
        run.add(_stage(order[0], "fail", problem))
        run.skip_rest("skipped: configuration incomplete")
    elif body.kind == "ldap":
        run = await _ldap_test(cfg, secret, body.test_username, body.test_password)
    else:
        run = await _oidc_test(cfg, body.provider_id)
    ok = run.ok
    await audit.record(
        "idp.test_draft",
        actor=p,
        category="platform",
        object_name=str(cfg.get("server_uri") or cfg.get("issuer") or ""),
        result="success" if ok else "failure",
        failure_reason=None if ok else next((s["message"] for s in run.stages if s["status"] == "fail"), None),
        details={
            "kind": body.kind,
            "stages": [(s["stage"], s["status"]) for s in run.stages],
            "test_user": bool(body.test_username),
        },
    )
    return {"ok": ok, "stages": run.stages}
