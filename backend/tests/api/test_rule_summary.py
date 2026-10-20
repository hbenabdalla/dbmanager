"""Compliance counts come from the database over every visible open finding, per rule and per
server (the page used to count only the first 500 findings in the browser)."""

from __future__ import annotations

import pytest

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _all_findings(client, path: str) -> list[dict]:  # type: ignore[no-untyped-def, type-arg]
    items, page = [], 1
    while True:
        r = (await client.get(path, params={"page": page, "page_size": 100, "source": "all"})).json()
        items += r["items"]
        if len(items) >= r["total"] or not r["items"]:
            return items
        page += 1


async def test_rule_summary_matches_the_findings(admin, servers, make_client) -> None:  # type: ignore[no-untyped-def]
    for kind, path in (("security", "/api/v1/security/findings"), ("performance", "/api/v1/recommendations")):
        summary = (await admin.get("/api/v1/advisor/rule-summary", params={"kind": kind, "source": "all"})).json()
        findings = await _all_findings(admin, path)
        assert findings, kind
        expected: dict[str, set[str]] = {}
        for f in findings:
            expected.setdefault(f["rule_id"], set()).add(f["server_id"])
        assert {k: v["servers"] for k, v in summary.items()} == {k: len(v) for k, v in expected.items()}
        assert sum(v["findings"] for v in summary.values()) == len(findings)
    # scoped: the Development DBA only counts findings of Development servers
    dev = await make_client(("dba.dev", DEMO_PW))
    scoped = (await dev.get("/api/v1/advisor/rule-summary", params={"kind": "security", "source": "all"})).json()
    full = (await admin.get("/api/v1/advisor/rule-summary", params={"kind": "security", "source": "all"})).json()
    assert sum(v["findings"] for v in scoped.values()) < sum(v["findings"] for v in full.values())
    monitor = await make_client(("monitor", DEMO_PW))
    assert (await monitor.get("/api/v1/advisor/rule-summary", params={"kind": "security"})).status_code == 403
