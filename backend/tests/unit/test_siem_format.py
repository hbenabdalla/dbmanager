"""RFC 5424 formatting of forwarded audit events (sprint 0.11.4)."""

from __future__ import annotations

import json


def test_rfc5424_message_layout_and_escaping() -> None:
    from app.services import siem

    ev = {
        "id": 42,
        "ts": "2026-10-19T08:15:30.123456+03:00",
        "action": "admin.drop_user",
        "category": "admin",
        "result": "success",
        "username": "dba",
        "source_ip": "10.0.0.7",
        "server_name": 'PG"PROD]1',
        "risk_class": "DESTRUCTIVE",
        "details": {"statements": ["DROP ROLE x"]},
    }
    raw = siem.rfc5424(ev, {"facility": "local4", "app_name": "dbm prod"}, hostname="dbm-1")
    header, _, msg = raw.partition(b"\xef\xbb\xbf")
    h = header.decode()
    assert h.startswith('<165>1 2026-10-19T05:15:30.123Z dbm-1 dbmprod - admin.drop_user [dbm@32473 id="42" ')
    assert r'server="PG\"PROD\]1"' in h and 'risk="DESTRUCTIVE"' in h  # PARAM-VALUE escaping
    assert json.loads(msg)["details"]["statements"] == ["DROP ROLE x"]
    assert siem.severity({"result": "denied"}) == 4 and siem.severity({"result": "success", "category": "auth"}) == 5
    assert siem.rfc5424({**ev, "result": "failure"}, {}, hostname="h").startswith(b"<108>1 ")  # audit(13)*8+4
