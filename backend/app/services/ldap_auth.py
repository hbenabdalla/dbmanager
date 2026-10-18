"""LDAP / Active Directory authentication.

Flow: bind with the service account → search the user entry with an escaped filter →
bind as the user DN with the supplied password → read group membership → map groups.
"""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import EncryptedBlob, get_keyring
from app.models import IdentityProvider, User
from app.services.identity_sync import provision_external_user


@dataclass
class LdapEntry:
    dn: str
    username: str
    email: str | None
    display_name: str | None
    groups: list[str]


def provider_secret(p: IdentityProvider) -> str | None:
    if not p.secret:
        return None
    return get_keyring().decrypt(EncryptedBlob.from_dict(p.secret), f"idp:{p.id}")


def _server(cfg: dict[str, Any]):  # type: ignore[no-untyped-def]
    from ldap3 import Server, Tls

    uri: str = cfg["server_uri"]
    tls = None
    if uri.startswith("ldaps://") or cfg.get("start_tls"):
        validate = ssl.CERT_REQUIRED if cfg.get("verify_cert", True) else ssl.CERT_NONE
        tls = Tls(validate=validate, ca_certs_data=cfg.get("ca_pem") or None, version=ssl.PROTOCOL_TLS_CLIENT)
    return Server(uri, use_ssl=uri.startswith("ldaps://"), tls=tls, connect_timeout=int(cfg.get("timeout", 8)))


def _ldap_lookup_and_bind(
    cfg: dict[str, Any], bind_password: str | None, username: str, password: str
) -> LdapEntry | None:
    from ldap3 import SUBTREE, Connection
    from ldap3.utils.conv import escape_filter_chars

    server = _server(cfg)
    svc = Connection(
        server,
        user=cfg.get("bind_dn") or None,
        password=bind_password or None,
        receive_timeout=int(cfg.get("timeout", 8)),
        raise_exceptions=False,
    )
    if cfg.get("start_tls") and not cfg["server_uri"].startswith("ldaps://"):
        svc.open()
        svc.start_tls()
    if not svc.bind():
        raise RuntimeError("LDAP service bind failed")
    try:
        flt = cfg.get("user_filter", "(&(objectClass=person)(uid={username}))").replace(
            "{username}", escape_filter_chars(username)
        )
        attr_user = cfg.get("attr_username", "uid")
        attr_mail = cfg.get("attr_email", "mail")
        attr_name = cfg.get("attr_display_name", "displayName")
        group_attr = cfg.get("group_attribute", "memberOf")
        svc.search(
            cfg["user_base_dn"], flt, SUBTREE, attributes=[attr_user, attr_mail, attr_name, group_attr], size_limit=2
        )
        if len(svc.entries) != 1:
            return None
        e = svc.entries[0]
        dn = e.entry_dn

        def _val(a: str) -> str | None:
            return str(e[a].value) if a in e and e[a].value else None

        groups: list[str] = [str(g) for g in (e[group_attr].values if group_attr in e else [])]
        if cfg.get("group_search_base"):
            gflt = cfg.get("group_filter", "(member={user_dn})").replace("{user_dn}", escape_filter_chars(dn))
            svc.search(cfg["group_search_base"], gflt, SUBTREE, attributes=["cn"])
            groups += [g.entry_dn for g in svc.entries]
    finally:
        svc.unbind()

    user_conn = Connection(
        server, user=dn, password=password, raise_exceptions=False, receive_timeout=int(cfg.get("timeout", 8))
    )
    if cfg.get("start_tls") and not cfg["server_uri"].startswith("ldaps://"):
        user_conn.open()
        user_conn.start_tls()
    ok = user_conn.bind()
    user_conn.unbind()
    if not ok:
        return None
    return LdapEntry(
        dn=dn, username=_val(attr_user) or username, email=_val(attr_mail), display_name=_val(attr_name), groups=groups
    )


async def ldap_login(db: AsyncSession, provider: IdentityProvider, username: str, password: str) -> User | None:
    if not password:  # an empty password would be an unauthenticated bind
        return None
    entry = await asyncio.to_thread(
        _ldap_lookup_and_bind, provider.config, provider_secret(provider), username, password
    )
    if entry is None:
        return None
    return await provision_external_user(
        db,
        provider,
        subject=entry.dn,
        username=entry.username,
        email=entry.email,
        display_name=entry.display_name,
        external_groups=entry.groups,
    )


async def test_provider(provider: IdentityProvider) -> dict[str, Any]:
    """Validate connectivity and the service bind (no user credentials involved)."""
    from ldap3 import Connection

    def _run() -> dict[str, Any]:
        conn = Connection(
            _server(provider.config),
            user=provider.config.get("bind_dn") or None,
            password=provider_secret(provider) or None,
            raise_exceptions=False,
        )
        ok = conn.bind()
        res = {"bind": ok, "result": conn.result.get("description") if conn.result else None}
        conn.unbind()
        return res

    return await asyncio.to_thread(_run)
