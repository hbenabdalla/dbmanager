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
