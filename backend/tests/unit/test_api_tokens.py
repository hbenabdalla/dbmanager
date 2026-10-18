"""API token helpers: format, hashing, interactive-only matching, scope intersection, CIDRs."""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import ValidationFailed
from app.core.security import token_hash
from app.domain.scopes import Scope, ScopeType
from app.services import api_tokens
from app.services.authz import Authorizer, Grant


def test_generate_and_parse() -> None:
    full, prefix, secret_hash = api_tokens.generate()
    parsed = api_tokens.parse(full)
    assert parsed is not None and parsed[0] == prefix
    assert token_hash(parsed[1]) == secret_hash and parsed[1] not in secret_hash
    assert len(secret_hash) == 64
    other, _, _ = api_tokens.generate()
    assert other != full
    for bad in ("", "dbm_", "Bearer x", f"dbx_{prefix}_{parsed[1]}", full + "x", full.upper()):
        assert api_tokens.parse(bad) is None


@pytest.mark.parametrize(
    ("method", "path", "denied"),
    [
        ("GET", "/api/v1/me/tokens", True),
        ("DELETE", "/api/v1/me/tokens/123", True),
        ("GET", "/api/v1/users/abc/tokens", True),
        ("POST", "/api/v1/auth/change-password", True),
        ("POST", "/api/v1/users/abc/reset-password", True),
        ("POST", "/api/v1/users", True),
        ("PATCH", "/api/v1/roles/x", True),
        ("POST", "/api/v1/role-assignments", True),
        ("DELETE", "/api/v1/identity-providers/x", True),
        ("GET", "/api/v1/users", False),
        ("GET", "/api/v1/roles", False),
        ("GET", "/api/v1/servers", False),
        ("POST", "/api/v1/servers/x/collect", False),
    ],
)
def test_interactive_only(method: str, path: str, denied: bool) -> None:
    assert api_tokens.is_interactive_only(method, path) is denied


def test_restrict_intersects_every_grant() -> None:
    uid = uuid.uuid4()
    env = uuid.uuid4()
    authz = Authorizer(
        user_id=uid,
        grants=[
            Grant(Scope(ScopeType.PLATFORM, "*"), frozenset({"server.view", "alerts.view"}), "A"),
            Grant(
                Scope(ScopeType.ENVIRONMENT, str(env)),
                frozenset({"server.view", "performance.view", "session.view"}),
                "B",
            ),
        ],
    )
    assert api_tokens.restrict(authz, None) is authz
    r = api_tokens.restrict(authz, ["server.view", "performance.view", "users.manage"])
    assert r.all_permissions() == {"server.view", "performance.view"}  # never more than the owner holds
    assert r.grants[1].scope == authz.grants[1].scope  # scopes of grants are kept
    assert not r.has_anywhere("alerts.view")


def test_cidrs() -> None:
    assert api_tokens.validate_cidrs(None) is None
    assert api_tokens.validate_cidrs(["10.1.2.3/8", " 2001:db8::/32 "]) == ["10.0.0.0/8", "2001:db8::/32"]
    with pytest.raises(ValidationFailed):
        api_tokens.validate_cidrs(["999.1.1.1/8"])
    assert api_tokens.ip_allowed(None, None)
    assert api_tokens.ip_allowed(["10.0.0.0/8"], "10.9.9.9")
    assert not api_tokens.ip_allowed(["10.0.0.0/8"], "192.168.1.1")
    assert not api_tokens.ip_allowed(["10.0.0.0/8"], None)
    assert not api_tokens.ip_allowed(["10.0.0.0/8"], "garbage")
