"""Operator-supplied engine logos: only whitelisted file names are served, with nosniff and a
sandboxing CSP; without a branding directory nothing is served."""

from __future__ import annotations

import os

import pytest

from app.core.config import get_settings

pytestmark = pytest.mark.asyncio


async def test_no_branding_dir_serves_nothing(admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "branding_dir", "")
    assert (await admin.get("/api/v1/branding/engine-logos")).json() == {"engines": []}
    assert (await admin.get("/api/v1/branding/engine-logos/postgresql")).status_code == 404


async def test_whitelisted_logos_only(admin, make_client, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "oracle.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    (tmp_path / "mysql.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    (tmp_path / "secret.txt").write_text("not served")
    (tmp_path / "db2.svg").write_bytes(b"x" * (600 * 1024))  # too large
    os.symlink(tmp_path / "secret.txt", tmp_path / "mariadb.svg")
    monkeypatch.setattr(get_settings(), "branding_dir", str(tmp_path))

    assert (await admin.get("/api/v1/branding/engine-logos")).json() == {"engines": ["mysql", "oracle"]}
    r = await admin.get("/api/v1/branding/engine-logos/oracle")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert "script-src" not in r.headers["content-security-policy"]
    assert (await admin.get("/api/v1/branding/engine-logos/mysql")).headers["content-type"] == "image/png"
    for bad in ("secret.txt", "secret", "mariadb", "db2", "..%2Fsecret.txt", "unknown"):
        assert (await admin.get(f"/api/v1/branding/engine-logos/{bad}")).status_code == 404, bad
    anon = await make_client()
    assert (await anon.get("/api/v1/branding/engine-logos")).status_code == 401
