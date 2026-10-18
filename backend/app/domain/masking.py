"""SQL literal masking for display of query text to users without ``query.view_text``."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\$([A-Za-z_][A-Za-z_0-9]*|)\$.*?\$\1\$", re.S), "?"),  # dollar-quoted
    (re.compile(r"[EeNn]?'(?:[^']|'')*'"), "?"),  # string literals (incl. E'', N'')
    (re.compile(r"(?<![\w$.])-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w$])"), "?"),  # numbers
    (re.compile(r"\bX'[0-9A-Fa-f]*'"), "?"),
]

_SECRET_HINT = re.compile(r"(password|identified\s+by|secret)\s*(=|\s)\s*\S+", re.I)


def mask_sql(sql: str | None, max_len: int = 20_000) -> str:
    if not sql:
        return ""
    out = sql[:max_len]
    out = _SECRET_HINT.sub(lambda m: f"{m.group(1)} ?", out)
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def redact_secret_clauses(sql: str) -> str:
    """Always applied before persisting user SQL (history/audit): hides inline passwords."""
    return re.sub(r"(password|identified\s+by)\s*(=\s*)?'(?:[^']|'')*'", r"\1 '***'", sql, flags=re.I)
