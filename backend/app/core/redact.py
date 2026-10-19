"""Redaction of credentials from free text and structured data (diagnostic bundles, driver
messages, exported reports). Conservative: anything that looks like a secret is replaced by
``***``; the surrounding text is kept so the message remains useful."""

from __future__ import annotations

import re
from typing import Any

MASK = "***"
SENSITIVE_KEYS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "master_key",
    "private_key",
    "api_key",
    "apikey",
    "access_key",
    "credential",
    "passphrase",
    "identity",
)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # PEM private keys
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), MASK),
    # age identities
    (re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]{50,}"), MASK),
    # user:password@ in URLs and DSNs
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^:/@\s]+):[^@\s]+@"), r"\1:" + MASK + "@"),
    # key=value / key: value pairs (connection strings, ODBC, CLI, query strings)
    (
        re.compile(
            r"(?i)\b(pwd|password|passwd|secret|token|api[_-]?key|access[_-]?key|secret[_-]?key|passphrase)"
            r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\{[^}]*\}|[^;\s,&\"']+)"
        ),
        r"\1\2" + MASK,
    ),
    # HTTP auth headers
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 " + MASK),
    # DBManager API tokens
    (re.compile(r"\bdbm_[A-Za-z0-9]{6,}_[A-Za-z0-9_\-]{16,}"), "dbm_" + MASK),
]


def text(value: str | None, limit: int | None = None) -> str | None:
    if value is None:
        return None
    out = value
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out[:limit] if limit else out


def is_sensitive_key(key: str) -> bool:
    k = key.lower()
    return any(s in k for s in SENSITIVE_KEYS)


def data(value: Any) -> Any:
    """Recursively redact a JSON-like structure: values under sensitive keys and secrets in text."""
    if isinstance(value, dict):
        # strings and structures under sensitive keys are masked; presence flags such as
        # ``has_secret: true`` and empty values are kept
        return {
            k: (MASK if is_sensitive_key(str(k)) and isinstance(v, str | dict | list) and v else data(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [data(v) for v in value]
    if isinstance(value, str):
        return text(value)
    return value
