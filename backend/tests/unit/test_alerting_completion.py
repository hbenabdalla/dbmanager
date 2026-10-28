"""Alerting completion (sprints 0.10.1–0.10.4): tag scope, range aggregation, recurring maintenance
windows across time zones and daylight saving, channel routing, webhook presets, SMTP security
modes, retry backoff."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services import alerting, notify

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
BERLIN = ZoneInfo("Europe/Berlin")


def server(**kw):  # type: ignore[no-untyped-def]
    base = dict(
        id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        engine="postgresql",
        tags=[SimpleNamespace(key="Team", value="payments"), SimpleNamespace(key="tier", value="gold")],
        latest_metrics={},
        collection_interval_s=60,
        is_demo=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def window(**kw):  # type: ignore[no-untyped-def]
    base = dict(
        name="w",
        scope_type="all",
        scope_id=None,
        recurrence="none",
        weekdays=[],
        time_zone="UTC",
        repeat_until=None,
        starts_at=NOW - timedelta(hours=1),
        ends_at=NOW + timedelta(hours=1),
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ---- tag scope ------------------------------------------------------------------------------------
def test_tag_scope_matches_key_or_exact_value() -> None:
    s = server()
    assert alerting.in_scope("tag", "team", s)  # key only, case-insensitive key
    assert alerting.in_scope("tag", "Team=payments", s)
    assert not alerting.in_scope("tag", "team=Payments", s)  # values are exact
    assert not alerting.in_scope("tag", "owner", s)
    assert not alerting.in_scope("tag", None, s)


# ---- range aggregation ------------------------------------------------------------------------------
def test_range_rule_uses_the_window_aggregate() -> None:
    s = server()
    r = SimpleNamespace(
        kind="metric", metric="cpu.percent", operator=">", threshold=80.0, aggregation="avg", window_seconds=900
    )
    holds, value, desc = alerting.condition(r, s, NOW, {str(s.id): 85.5})
    assert holds is True and value == 85.5 and desc.startswith("avg(cpu.percent, 15m) = 85.5")
    assert alerting.condition(r, s, NOW, {str(s.id): 70.0})[0] is False
    assert alerting.condition(r, s, NOW, {})[0] is None  # no samples in the window: keep state
    assert alerting.condition(r, s, NOW, None)[2] == "no data in the window"  # storage down


# ---- maintenance windows ------------------------------------------------------------------------------
def test_one_off_window_boundaries_are_half_open() -> None:
    w = window(starts_at=NOW, ends_at=NOW + timedelta(hours=1))
    assert alerting.window_active(w, NOW)  # starts exactly at starts_at
    assert alerting.window_active(w, NOW + timedelta(minutes=59, seconds=59))
    assert not alerting.window_active(w, NOW + timedelta(hours=1))  # ends exactly at ends_at
    assert not alerting.window_active(w, NOW - timedelta(seconds=1))
    assert alerting.window_state(w, NOW - timedelta(minutes=5))[0] == "scheduled"
    assert alerting.window_state(w, NOW + timedelta(hours=2))[0] == "ended"


def test_window_given_with_a_non_utc_offset() -> None:
    plus3 = datetime(2026, 10, 18, 15, 0, tzinfo=ZoneInfo("Asia/Riyadh"))  # == 12:00 UTC
    w = window(starts_at=plus3, ends_at=plus3 + timedelta(minutes=30))
    assert alerting.window_active(w, NOW + timedelta(minutes=10))
    assert not alerting.window_active(w, NOW + timedelta(minutes=31))


def test_daily_window_keeps_local_time_across_daylight_saving() -> None:
    # 02:00–03:00 Berlin every night, first occurrence in summer time (UTC+2)
    first = datetime(2026, 10, 20, 2, 0, tzinfo=BERLIN)
    w = window(recurrence="daily", time_zone="Europe/Berlin", starts_at=first, ends_at=first + timedelta(hours=1))
    summer = datetime(2026, 10, 21, 2, 30, tzinfo=BERLIN)  # 00:30 UTC
    winter = datetime(2026, 10, 27, 2, 30, tzinfo=BERLIN)  # after 25 Oct: 01:30 UTC
    assert alerting.window_active(w, summer.astimezone(UTC))
    assert alerting.window_active(w, winter.astimezone(UTC))
    # the UTC instant that was 02:30 in summer is 01:30 local in winter: outside the window
    assert not alerting.window_active(w, datetime(2026, 10, 27, 0, 30, tzinfo=UTC))
    assert not alerting.window_active(w, datetime(2026, 10, 19, 2, 30, tzinfo=BERLIN).astimezone(UTC))  # before first


def test_daily_window_spanning_midnight_and_repeat_until() -> None:
    first = datetime(2026, 10, 18, 23, 0, tzinfo=UTC)
    w = window(
        recurrence="daily",
        starts_at=first,
        ends_at=first + timedelta(hours=2),
        repeat_until=datetime(2026, 10, 21, 0, 0, tzinfo=UTC),
    )
    assert alerting.window_active(w, datetime(2026, 10, 20, 0, 30, tzinfo=UTC))  # spill-over of the 19th
    assert alerting.window_active(w, datetime(2026, 10, 20, 23, 30, tzinfo=UTC))
    assert not alerting.window_active(w, datetime(2026, 10, 21, 23, 30, tzinfo=UTC))  # after repeat_until


def test_weekly_window_on_selected_weekdays() -> None:
    first = datetime(2026, 10, 19, 20, 0, tzinfo=UTC)  # Monday
    w = window(recurrence="weekly", weekdays=[1, 3], starts_at=first, ends_at=first + timedelta(hours=1))
    assert alerting.window_active(w, datetime(2026, 10, 21, 20, 30, tzinfo=UTC))  # Wednesday
    assert not alerting.window_active(w, datetime(2026, 10, 20, 20, 30, tzinfo=UTC))  # Tuesday
    state, start, _end = alerting.window_state(w, datetime(2026, 10, 22, 12, 0, tzinfo=UTC))
    assert state == "scheduled" and start == datetime(2026, 10, 26, 20, 0, tzinfo=UTC)  # next Monday


def test_scoped_recurring_window_selects_server() -> None:
    s = server()
    first = NOW - timedelta(minutes=30)
    w = window(
        recurrence="daily", scope_type="tag", scope_id="tier=gold", starts_at=first, ends_at=NOW + timedelta(hours=1)
    )
    assert alerting.active_window([w], s, NOW) is w
    assert alerting.active_window([w], server(tags=[]), NOW) is None


# ---- routing ------------------------------------------------------------------------------------------
def _channel(**kw):  # type: ignore[no-untyped-def]
    base = dict(id=uuid.uuid4(), min_severity="info", routes={})
    base.update(kw)
    return SimpleNamespace(**base)


def test_channel_routing_filters() -> None:
    s = server(engine="oracle")
    listed = _channel()
    rule = SimpleNamespace(channel_ids=[str(listed.id)])
    high = SimpleNamespace(severity="high")
    assert alerting.channel_accepts(listed, rule, high, s)
    assert not alerting.channel_accepts(_channel(), rule, high, s)  # not listed, not subscribed
    assert alerting.channel_accepts(_channel(routes={"subscribe_all": True}), rule, high, s)
    # severity floor
    assert not alerting.channel_accepts(_channel(id=listed.id, min_severity="critical"), rule, high, s)
    # environment, tag and engine filters narrow what a channel receives
    env_ok = _channel(routes={"subscribe_all": True, "environment_ids": [str(s.environment_id)]})
    env_no = _channel(routes={"subscribe_all": True, "environment_ids": [str(uuid.uuid4())]})
    assert alerting.channel_accepts(env_ok, rule, high, s) and not alerting.channel_accepts(env_no, rule, high, s)
    assert alerting.channel_accepts(_channel(routes={"subscribe_all": True, "tags": ["team=payments"]}), rule, high, s)
    assert not alerting.channel_accepts(_channel(routes={"subscribe_all": True, "tags": ["team=web"]}), rule, high, s)
    assert not alerting.channel_accepts(_channel(routes={"subscribe_all": True, "engines": ["mysql"]}), rule, high, s)


# ---- presets ------------------------------------------------------------------------------------------
ALERT = {
    "title": "CPU on ORA-PROD-01",
    "message": "avg(cpu.percent, 15m) = 91 (> 80)",
    "server_name": "ORA-PROD-01",
    "engine": "oracle",
    "environment": "Production",
    "severity": "critical",
    "value": 91.0,
    "operator": ">",
    "threshold": 80.0,
    "renotify": False,
    "url": "https://dbm.example.com/alerts?id=1",
}


def test_slack_preset_payload() -> None:
    body = notify.preset_payload("slack", "firing", ALERT)
    assert body["text"] == "CRITICAL: CPU on ORA-PROD-01"
    assert body["blocks"][0] == {
        "type": "header",
        "text": {"type": "plain_text", "text": "CRITICAL: CPU on ORA-PROD-01"},
    }
    fields = {f["text"].split("\n")[0]: f["text"].split("\n")[1] for f in body["blocks"][2]["fields"]}
    assert fields["*Server*"] == "ORA-PROD-01 (oracle)" and fields["*Value*"] == "91 (> 80.0)"
    assert body["blocks"][3]["elements"][0]["url"] == ALERT["url"]


def test_teams_preset_payload_is_an_adaptive_card_message() -> None:
    body = notify.preset_payload("teams", "resolved", ALERT)
    assert body["type"] == "message"
    att = body["attachments"][0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = att["content"]
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.4"
    assert card["body"][0]["text"] == "RESOLVED: CPU on ORA-PROD-01"
    assert {"title": "Environment", "value": "Production"} in card["body"][2]["facts"]
    assert card["actions"][0] == {"type": "Action.OpenUrl", "title": "Open in KBM DBManager", "url": ALERT["url"]}


def test_renotification_is_marked_and_test_message_works_for_every_preset() -> None:
    again = notify.preset_payload("slack", "firing", {**ALERT, "renotify": True})
    assert again["text"].endswith("(still firing)")
    for preset in notify.PRESETS:
        assert notify.preset_payload(preset, "test", None)
    with pytest.raises(notify.DeliveryError):
        notify.preset_payload("pagerduty", "firing", ALERT)


# ---- SMTP security and retries ---------------------------------------------------------------------------
def test_smtp_security_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    s = SimpleNamespace(smtp_security="", smtp_starttls=True)
    monkeypatch.setattr(notify, "get_settings", lambda: s)
    assert notify.smtp_security({}) == "starttls"
    assert notify.smtp_security({"security": "tls"}) == "tls"
    assert notify.smtp_security({"starttls": False}) == "none"  # legacy channel setting
    s.smtp_security = "tls"
    assert notify.smtp_security({}) == "tls"
    s.smtp_security, s.smtp_starttls = "", False
    assert notify.smtp_security({}) == "none"


def test_retry_backoff_and_retryable_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        alerting, "get_settings", lambda: SimpleNamespace(notify_retry_backoff_seconds=["60", "300", "900"])
    )
    assert [alerting.backoff_seconds(n) for n in (1, 2, 3, 4, 9)] == [60, 300, 900, 900, 900]
    assert alerting._retryable(notify.DeliveryError("timeout"))
    assert alerting._retryable(notify.DeliveryError("busy", 503))
    assert alerting._retryable(notify.DeliveryError("slow down", 429))
    assert not alerting._retryable(notify.DeliveryError("bad request", 400))
    assert not alerting._retryable(notify.DeliveryError("gone", 410))
