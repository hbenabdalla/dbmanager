from app.core.security import hash_password, password_policy_errors, verify_password
from app.domain.masking import mask_sql, redact_secret_clauses
from app.services.audit import sanitize


def test_mask_literals() -> None:
    masked = mask_sql("SELECT * FROM users WHERE email = 'a@b.com' AND id = 42 AND x = $$secret$$")
    assert "a@b.com" not in masked and "42" not in masked and "secret" not in masked
    assert "users" in masked and "email" in masked


def test_mask_keeps_identifiers_with_digits() -> None:
    assert "t1" in mask_sql("select col2 from t1")


def test_redact_password_clause() -> None:
    out = redact_secret_clauses("CREATE ROLE bob LOGIN PASSWORD 'hunter2'")
    assert "hunter2" not in out


def test_argon2id() -> None:
    h = hash_password("Correct-Horse-9")
    assert h.startswith("$argon2id$")
    assert verify_password("Correct-Horse-9", h)
    assert not verify_password("wrong", h)
    assert not verify_password("anything", None)


def test_password_policy() -> None:
    assert password_policy_errors("short")
    assert password_policy_errors("alllowercaseletters")
    assert not password_policy_errors("Good-Passw0rd-2026")


def test_audit_sanitize() -> None:
    out = sanitize(
        {"username": "x", "password": "p", "nested": {"client_secret": "s", "ok": 1}, "list": [{"token": "t"}]}
    )
    assert out["password"] == "***" and out["nested"]["client_secret"] == "***" and out["list"][0]["token"] == "***"
    assert out["nested"]["ok"] == 1
