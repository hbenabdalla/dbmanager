"""Part B backend: setup wizard, staged connection tests, bulk onboarding, IdP draft tests."""

from __future__ import annotations

import csv
import io
import json
import os

import pytest
from sqlalchemy.engine import make_url

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")

# the scratch PostgreSQL the suite runs against (local default or CI service), see tests/conftest.py
_URL = make_url(os.environ["DBM_TEST_DATABASE_URL"])
PG = {
    "engine": "postgresql",
    "host": _URL.host or "localhost",
    "port": _URL.port or 5432,
    "username": _URL.username,
    "password": _URL.password,
    "default_database": _URL.database,
    "tls_mode": "disable",
}


def _events(text: str) -> list[dict]:  # type: ignore[type-arg]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _terminal(events: list[dict]) -> list[tuple[str, str]]:  # type: ignore[type-arg]
    return [(e["stage"], e["status"]) for e in events if e["stage"] != "result" and e["status"] != "running"]


# ---- setup wizard -------------------------------------------------------------------------
async def test_setup_state_progress_and_complete(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    st = (await admin.get("/api/v1/platform/setup")).json()
    assert st["steps"][0] == "welcome" and st["steps"][-1] == "complete" and "organization" in st
    r = await admin.put(
        "/api/v1/platform/setup",
        json={
            "current_step": "sites",
            "completed_steps": ["welcome", "admin", "organization"],
            "data": {"sites": {"names": ["DC1"], "bind_password": "must-not-persist"}},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_step"] == "sites" and body["completed_steps"] == ["welcome", "admin", "organization"]
    assert body["data"]["sites"]["names"] == ["DC1"] and "must-not-persist" not in r.text
    assert (await admin.put("/api/v1/platform/setup", json={"current_step": "nope"})).status_code == 422

    old = body["organization"]
    r = await admin.post("/api/v1/platform/setup/organization", json={"name": "Acme DB Ops", "timezone": "Asia/Riyadh"})
    assert r.status_code == 200, r.text
    assert r.json()["organization"]["timezone"] == "Asia/Riyadh" and r.json()["organization"]["name"] == "Acme DB Ops"
    bad = await admin.post("/api/v1/platform/setup/organization", json={"name": "Acme", "timezone": "Mars/Olympus"})
    assert bad.status_code == 422
    await admin.post("/api/v1/platform/setup/organization", json={"name": old["name"], "timezone": old["timezone"]})

    r = await admin.post("/api/v1/platform/setup/monitoring", json={"profile": "light"})
    assert r.status_code == 200 and r.json()["intervals"]["collection_interval_s"] == 300
    settings = (await admin.get("/api/v1/settings")).json()["editable"]
    assert settings["collection.default_interval_s"] == 300
    await admin.post("/api/v1/platform/setup/monitoring", json={"profile": "standard"})

    done = (await admin.post("/api/v1/platform/setup/complete")).json()
    assert done["completed"] is True and done["needs_setup"] is False and done["current_step"] == "complete"

    mon = await make_client(("monitor", DEMO_PW))
    limited = (await mon.get("/api/v1/platform/setup")).json()
    assert set(limited) == {"completed", "needs_setup", "steps"} and limited["needs_setup"] is False
    assert (await mon.put("/api/v1/platform/setup", json={"current_step": "sites"})).status_code == 403
    assert (await mon.post("/api/v1/platform/setup/complete")).status_code == 403
    events = (await admin.get("/api/v1/audit", params={"action": "setup.complete"})).json()["items"]
    assert events


async def test_setup_checklist(admin) -> None:  # type: ignore[no-untyped-def]
    c = (await admin.get("/api/v1/platform/setup/checklist")).json()
    ids = [i["id"] for i in c["items"]]
    assert ids[:2] == ["admin_password_changed", "organization"] and "drivers_healthy" in ids
    assert c["total"] == len(ids) and 0 <= c["done"] <= c["total"]
    sim = next(i for i in c["items"] if i["id"] == "simulation_off")
    assert sim["optional"] is True and sim["done"] is False  # the CI fleet is simulated
    assert all(i["href"] for i in c["items"])


# ---- staged test connection ---------------------------------------------------------------
async def test_stream_real_postgres_all_stages(admin) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post("/api/v1/servers/test-connection/stream", json=PG)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    ev = _events(r.text)
    order = [e["stage"] for e in ev if e["status"] == "running"]
    assert order == ["validate", "driver", "dns", "tcp", "tls", "authenticate", "version", "privileges", "capabilities"]
    term = dict(_terminal(ev))
    assert term["tcp"] == "ok" and term["authenticate"] == "ok" and term["tls"] == "warn"  # tls_mode=disable
    assert ev[-1]["stage"] == "result" and ev[-1]["status"] == "ok" and ev[-1]["result"]["version"]
    assert PG["password"] not in r.text


async def test_stream_refused_port_stops_at_tcp(admin) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post("/api/v1/servers/test-connection/stream", json={**PG, "port": 1})
    term = _terminal(_events(r.text))
    assert term[:4] == [("validate", "ok"), ("driver", "ok"), ("dns", "ok"), ("tcp", "fail")]
    assert all(s == "skipped" for _, s in term[4:]) and len(term) == 9
    assert _events(r.text)[-1]["result"]["error_code"] == "CONNECTION_FAILED"


async def test_stream_wrong_password_fails_authentication(admin) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post("/api/v1/servers/test-connection/stream", json={**PG, "password": "wrong-pw-1"})
    term = dict(_terminal(_events(r.text)))
    assert term["tcp"] == "ok" and term["authenticate"] == "fail" and term["version"] == "skipped"
    assert _events(r.text)[-1]["result"]["error_code"] == "AUTHENTICATION_FAILED"


async def test_stream_unknown_host_and_bad_engine(admin) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post("/api/v1/servers/test-connection/stream", json={**PG, "host": "no-such-host.invalid"})
    assert dict(_terminal(_events(r.text)))["dns"] == "fail"
    r = await admin.post("/api/v1/servers/test-connection/stream", json={**PG, "engine": "foodb"})
    term = _terminal(_events(r.text))
    assert term[0] == ("validate", "fail") and all(s == "skipped" for _, s in term[1:])


async def test_stream_registered_simulated_server(admin, servers) -> None:  # type: ignore[no-untyped-def]
    sid = servers["PG-PROD-01"]["id"]
    r = await admin.post(f"/api/v1/servers/{sid}/test-connection/stream")
    term = dict(_terminal(_events(r.text)))
    assert term["dns"] == "skipped" and term["tcp"] == "skipped" and term["authenticate"] == "ok"
    assert _events(r.text)[-1]["status"] == "ok"


async def test_stream_requires_permission(make_client) -> None:  # type: ignore[no-untyped-def]
    mon = await make_client(("monitor", DEMO_PW))
    r = await mon.post("/api/v1/servers/test-connection/stream", json=PG)
    assert r.status_code == 403


# ---- bulk onboarding -----------------------------------------------------------------------
async def test_bulk_template_has_no_password_column(admin) -> None:  # type: ignore[no-untyped-def]
    r = await admin.get("/api/v1/servers/bulk/template.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    header = next(csv.reader(io.StringIO(r.text)))
    assert header[:4] == ["name", "engine", "host", "port"] and "credential" in header
    assert not any("pass" in h or "secret" in h for h in header)


async def test_bulk_validate_rejects_secrets_duplicates_unknowns(admin) -> None:  # type: ignore[no-untyped-def]
    rows = [
        {"name": "bulk-ok-1", "engine": "postgresql", "host": "10.20.0.1", "port": "5432", "environment": "Test"},
        {
            "name": "bulk-pw",
            "engine": "postgresql",
            "host": "10.20.0.2",
            "port": 5432,
            "environment": "Test",
            "password": "Plain-Text-1",
        },
        {"name": "bulk-ok-1", "engine": "postgresql", "host": "10.20.0.1", "port": 5432, "environment": "Test"},
        {"name": "bulk-env", "engine": "mysql", "host": "10.20.0.3", "environment": "Mars"},
        {"name": "bulk bad name", "engine": "nosuchdb", "host": "x", "port": 1, "environment": "Test"},
        {
            "name": "bulk-cred",
            "engine": "oracle",
            "host": "10.20.0.4",
            "port": 1521,
            "environment": "Test",
            "credential": "env:NOT_A_USERREF",
            "tls_mode": "prefer",
            "site": "Bulk-New-Site",
        },
    ]
    r = await admin.post("/api/v1/servers/bulk/validate", json={"rows": rows})
    assert r.status_code == 200, r.text
    out = r.json()
    assert "Plain-Text-1" not in r.text
    by = {row["index"]: row for row in out["rows"]}
    assert by[0]["ok"] and any(w["field"] == "credential" for w in by[0]["warnings"])
    assert not by[1]["ok"] and any("Never put passwords" in e["message"] for e in by[1]["errors"])
    assert not by[2]["ok"] and any(
        "duplicate" in e["message"] or "same endpoint" in e["message"] for e in by[2]["errors"]
    )
    assert not by[3]["ok"] and any(e["field"] == "environment" for e in by[3]["errors"])
    assert any(w["field"] == "port" for w in by[3]["warnings"])  # defaulted to 3306
    assert not by[4]["ok"] and {e["field"] for e in by[4]["errors"]} >= {"engine"}
    assert not by[5]["ok"] and any(e["field"] == "credential" for e in by[5]["errors"])
    assert any(w["field"] == "site" for w in by[5]["warnings"]) and any(
        w["field"] == "tls_mode" for w in by[5]["warnings"]
    )
    assert out["summary"] == {"total": 6, "valid": 1, "invalid": 5, "warnings": out["summary"]["warnings"]}


async def test_bulk_create_monitoring_only_and_copies_credential(admin) -> None:  # type: ignore[no-untyped-def]
    # a source server with a monitoring credential to copy from
    envs = {e["name"]: e["id"] for e in (await admin.get("/api/v1/environments")).json()}
    src = await admin.post(
        "/api/v1/servers",
        json={
            "name": "bulk-src",
            "engine": "postgresql",
            "host": "10.21.0.1",
            "port": 5432,
            "environment_id": envs["Test"],
            "monitoring_enabled": False,
            "monitoring_credential": {"username": "mon", "password": "Src-Secret-Value-9"},
        },
    )
    assert src.status_code == 201, src.text
    rows = [
        {
            "name": "bulk-new-1",
            "engine": "postgresql",
            "host": "10.21.0.2",
            "port": 5432,
            "environment": "Test",
            "site": "Bulk-Site-A",
            "credential": "server:bulk-src",
            "tags": "team=dba;tier=gold",
        },
        {
            "name": "bulk-new-2",
            "engine": "mysql",
            "host": "10.21.0.3",
            "port": 3306,
            "environment": "Test",
            "management_mode": "administration",
        },
        {"name": "bulk-new-3", "engine": "postgresql", "host": "10.21.0.4", "port": 5432, "environment": "Nope"},
    ]
    r = await admin.post("/api/v1/servers/bulk/create", json={"rows": rows, "monitoring_enabled": False})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created"] == 2 and out["failed"] == 1 and "Src-Secret-Value-9" not in r.text
    res = {x["name"]: x for x in out["results"]}
    assert res["bulk-new-3"]["ok"] is False and "environment" in res["bulk-new-3"]["error"]
    d1 = (await admin.get(f"/api/v1/servers/{res['bulk-new-1']['server_id']}")).json()
    assert d1["management_mode"] == "monitoring" and d1["site"]["name"] == "Bulk-Site-A"
    assert d1["credentials"]["monitoring"]["username"] == "mon" and d1["credentials"]["monitoring"]["has_secret"]
    assert {t["key"] for t in d1["tags"]} == {"team", "tier"}
    d2 = (await admin.get(f"/api/v1/servers/{res['bulk-new-2']['server_id']}")).json()
    assert d2["management_mode"] == "administration" and d2["credentials"]["monitoring"] is None
    # re-running the same file: everything is a duplicate now
    again = (await admin.post("/api/v1/servers/bulk/validate", json={"rows": rows[:2]})).json()
    assert again["summary"]["valid"] == 0
    for name, sid in (
        ("bulk-new-1", res["bulk-new-1"]["server_id"]),
        ("bulk-new-2", res["bulk-new-2"]["server_id"]),
        ("bulk-src", src.json()["id"]),
    ):
        assert (await admin.delete(f"/api/v1/servers/{sid}", params={"confirm": name})).status_code == 200
    assert (await admin.get("/api/v1/audit", params={"action": "server.bulk_register"})).json()["items"]


async def test_bulk_requires_register_permission(make_client) -> None:  # type: ignore[no-untyped-def]
    mon = await make_client(("monitor", DEMO_PW))
    assert (await mon.post("/api/v1/servers/bulk/validate", json={"rows": []})).status_code == 403
    assert (await mon.get("/api/v1/servers/bulk/template.csv")).status_code == 403


# ---- identity-provider drafts ----------------------------------------------------------------
async def test_idp_presets(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    r = await admin.get("/api/v1/identity-providers/presets")
    assert {p["key"] for p in r.json()} == {"keycloak", "entra", "okta", "generic"}
    assert all("openid" in p["scopes"] for p in r.json())
    mon = await make_client(("monitor", DEMO_PW))
    assert (await mon.get("/api/v1/identity-providers/presets")).status_code == 403


async def test_idp_draft_ldap_stages(admin) -> None:  # type: ignore[no-untyped-def]
    base = {
        "server_uri": "ldaps://no-such-ldap.invalid:636",
        "user_base_dn": "ou=people,dc=x",
        "bind_dn": "cn=svc,dc=x",
    }
    r = await admin.post(
        "/api/v1/identity-providers/test-draft", json={"kind": "ldap", "config": base, "secret": "Bind-Secret-77"}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is False and out["stages"][0]["stage"] == "dns" and out["stages"][0]["status"] == "fail"
    assert [s["stage"] for s in out["stages"]] == [
        "dns",
        "tcp",
        "tls",
        "bind",
        "user_search",
        "group_lookup",
        "user_bind",
    ]
    assert "Bind-Secret-77" not in r.text
    # reachable host, closed port
    r = await admin.post(
        "/api/v1/identity-providers/test-draft",
        json={"kind": "ldap", "config": {**base, "server_uri": "ldaps://localhost:1"}},
    )
    st = {s["stage"]: s["status"] for s in r.json()["stages"]}
    assert st["dns"] == "ok" and st["tcp"] == "fail" and st["bind"] == "skipped"
    # incomplete configuration
    r = await admin.post(
        "/api/v1/identity-providers/test-draft", json={"kind": "ldap", "config": {"server_uri": "ldaps://h"}}
    )
    assert r.json()["stages"][0]["status"] == "fail" and "user_base_dn" in r.json()["stages"][0]["message"]


async def test_idp_draft_oidc_stages(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post(
        "/api/v1/identity-providers/test-draft",
        json={
            "kind": "oidc",
            "config": {"issuer": "https://no-such-idp.invalid/realms/x", "client_id": "dbm"},
            "secret": "Client-Secret-55",
        },
    )
    out = r.json()
    assert out["ok"] is False and [s["stage"] for s in out["stages"]] == [
        "discovery",
        "issuer_match",
        "jwks",
        "endpoints",
        "redirect_uri",
    ]
    assert out["stages"][0]["status"] == "fail" and all(s["status"] == "skipped" for s in out["stages"][1:])
    assert "Client-Secret-55" not in r.text
    mon = await make_client(("monitor", DEMO_PW))
    assert (
        await mon.post("/api/v1/identity-providers/test-draft", json={"kind": "oidc", "config": {}})
    ).status_code == 403
