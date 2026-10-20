"""Credential redaction used by diagnostic bundles and driver error messages."""

from app.core import redact


def test_text_patterns() -> None:
    cases = {
        "DRIVER={ODBC};UID=sa;PWD=Str0ng!;Encrypt=yes": "DRIVER={ODBC};UID=sa;PWD=***;Encrypt=yes",
        "postgresql://dbm:s3cr3t@db.internal:5432/app": "postgresql://dbm:***@db.internal:5432/app",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.x.y": "Authorization: Bearer ***",
        'password = "two words" next': "password = *** next",
        "api_key: AKIAXXXXXXXXXXXX": "api_key: ***",
        "used dbm_abcdef1234_ABCDEFGHIJKLMNOPQRSTUV here": "used dbm_*** here",
        "secret_key={a;b}": "secret_key=***",
        "plain message, nothing to hide": "plain message, nothing to hide",
    }
    for raw, expected in cases.items():
        assert redact.text(raw) == expected, raw
    pem = "x -----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY----- y"
    assert redact.text(pem) == "x *** y"
    assert redact.text("AGE-SECRET-KEY-1" + "Q" * 58) == "***"
    assert redact.text(None) is None and len(redact.text("a" * 50, 10) or "") == 10


def test_structures_keep_presence_flags() -> None:
    out = redact.data(
        {
            "smtp_password": "x",
            "has_secret": True,
            "token": None,
            "nested": [{"Authorization": "Basic Zm9vOmJhcg=="}, "pwd=abc"],
            "count": 3,
        }
    )
    assert out == {
        "smtp_password": "***",
        "has_secret": True,
        "token": None,
        "nested": [{"Authorization": "***"}, "pwd=***"],
        "count": 3,
    }


def test_oracle_connect_strings_are_masked() -> None:
    from app.core import redact

    assert (
        redact.text("sqlplus scott/tiger@db.example.com:1521/ORCLPDB1")
        == "sqlplus scott/***@db.example.com:1521/ORCLPDB1"
    )
    assert "Oracle-Pass1" not in (redact.text("connect system/Oracle-Pass1@//host/svc") or "")
    assert redact.text("see https://x.example.com/a/b@c") == "see https://x.example.com/a/b@c"
