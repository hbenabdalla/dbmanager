"""CSV/formula injection guard shared by every CSV export (reports, audit log, workspace)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

#: a text cell starting with one of these is read as a formula by spreadsheet programs
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(v: Any) -> Any:
    """Text that a spreadsheet would evaluate (``=HYPERLINK(...)``, ``@SUM(...)``, ``+cmd|...``)
    is prefixed with an apostrophe. Numbers are left as they are; ``None`` becomes empty."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, str) and v.startswith(FORMULA_START):
        return "'" + v
    return v
