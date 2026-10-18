"""Documentation / GitHub / roadmap links shown in the Help menu and on the About page."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import DOCUMENTATION_URL
from app.api.v1 import about


def _links(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> dict[str, str | None]:
    values = {"documentation_url": "", "github_url": "https://github.com/hbenabdalla/dbmanager", "roadmap_url": ""}
    values.update(overrides)
    monkeypatch.setattr(about, "get_settings", lambda: SimpleNamespace(**values))
    return about.links()


def test_default_documentation_is_the_published_site(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DOCUMENTATION_URL == "https://db-manager.readthedocs.io/en/latest/"
    links = _links(monkeypatch)
    assert links["documentation"] == DOCUMENTATION_URL
    assert links["roadmap"] == "https://github.com/hbenabdalla/dbmanager/blob/main/ROADMAP.md"


def test_documentation_can_point_to_an_internal_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    links = _links(monkeypatch, documentation_url=" https://docs.example.internal/dbm/ ")
    assert links["documentation"] == "https://docs.example.internal/dbm/"


@pytest.mark.parametrize("value", ["off", "OFF", "none", "-"])
def test_documentation_entry_can_be_hidden(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    assert _links(monkeypatch, documentation_url=value)["documentation"] is None


def test_build_metadata_passes_through_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import __version__

    values = {
        "build_version": "0.11.0-rc1",
        "git_commit": "1c7a9da<script>alert(1)</script>",
        "build_date": "2026-10-18T09:00:00Z; rm -rf /",
    }
    monkeypatch.setattr(about, "get_settings", lambda: SimpleNamespace(**values))
    v = about.version_info("0006")
    assert v["application"] == v["backend"] == __version__
    assert v["build"] == "0.11.0-rc1" and v["schema"] == "0006"
    # only printable version characters survive; values are truncated
    assert v["git_commit"] == "1c7a9dascriptalert1script"
    assert v["build_date"] == "2026-10-18T09:00:00Zrm-rf"
    assert about._clean("x" * 100) == "x" * 64 and about._clean("") is None


async def test_public_version_endpoint_exposes_only_product_and_version() -> None:
    from app import __version__

    assert await about.version() == {"product": "DB Manager", "version": __version__}
