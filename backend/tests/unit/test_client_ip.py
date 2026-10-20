"""Client address from X-Forwarded-For: read right to left, trusted hops skipped, so a client
cannot choose its own address (token CIDR allow-lists, per-IP rate limits, audit)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api import deps


def _req(peer: str, xff: str | None = None) -> SimpleNamespace:
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


@pytest.fixture(autouse=True)
def _trusted(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(deps.get_settings(), "trusted_proxies", ["172.16.0.0/12", "127.0.0.1"])


def test_untrusted_peer_ignores_the_header() -> None:
    assert deps.client_ip(_req("203.0.113.7", "10.1.2.3")) == "203.0.113.7"


def test_spoofed_leftmost_value_is_ignored() -> None:
    # the client sent "10.1.2.3"; the trusted proxy appended the address it saw
    assert deps.client_ip(_req("172.18.0.5", "10.1.2.3, 203.0.113.9")) == "203.0.113.9"


def test_multi_hop_chain_skips_trusted_proxies() -> None:
    assert deps.client_ip(_req("172.18.0.5", "198.51.100.4, 172.20.0.2")) == "198.51.100.4"


def test_single_value_from_the_frontend_proxy() -> None:
    assert deps.client_ip(_req("172.18.0.5", "198.51.100.4")) == "198.51.100.4"
    assert deps.client_ip(_req("172.18.0.5", "172.18.0.1")) == "172.18.0.1"  # all hops trusted


def test_malformed_chain_falls_back_to_the_peer() -> None:
    assert deps.client_ip(_req("172.18.0.5", "198.51.100.4, not-an-ip")) == "172.18.0.5"
    assert deps.client_ip(_req("172.18.0.5", "")) == "172.18.0.5"
