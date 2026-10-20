"""Audit CSV export: user input cannot become a spreadsheet formula, and an export that hits the
row cap says so instead of silently dropping events."""

from __future__ import annotations

import csv
import io

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_export_neutralises_formulas_and_flags_truncation(admin, make_client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.api.v1 import audit as audit_api

    # a failed sign-in records the typed username verbatim
    with pytest.raises(Exception):  # noqa: B017,PT011 - the client raises on the 401
        await make_client(('=HYPERLINK("http://x")', "wrong-password"))
    r = await admin.get("/api/v1/audit/export.csv", params={"action": "auth.login"})
    assert r.status_code == 200 and r.headers["x-dbm-export-truncated"] == "false"
    rows = list(csv.reader(io.StringIO(r.text)))
    header, body = rows[0], rows[1:]
    names = [row[header.index("username")] for row in body]
    assert '\'=HYPERLINK("http://x")' in names
    assert not any(n.startswith(("=", "+", "-", "@")) for n in names)

    monkeypatch.setattr(audit_api, "EXPORT_MAX_ROWS", 1)
    r = await admin.get("/api/v1/audit/export.csv")
    assert r.headers["x-dbm-export-truncated"] == "true"
    assert len(list(csv.reader(io.StringIO(r.text)))) == 2  # header + one row
