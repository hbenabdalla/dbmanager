"""A scripted DB-API connection for adapter unit tests.

Routes map SQL fragments (substring or compiled regex, matched against whitespace-normalized
SQL) to responses: a list of row dicts, an exception to raise, or a callable taking the bound
parameters. Any statement without a route fails the test — so a test also proves the adapter
issues only the statements we reviewed.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

Response = list[dict[str, Any]] | BaseException | Callable[[Any], Any]


def norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


class FakeCursor:
    def __init__(self, conn: FakeConnection):
        self.conn = conn
        self.description: list[tuple[Any, ...]] | None = None
        self._rows: list[tuple[Any, ...]] = []
        self.rowcount = -1

    def execute(self, sql: str, params: Any = None) -> None:
        text = norm(sql)
        self.conn.log.append((text, params))
        resp = self.conn.respond(text, params)
        if callable(resp) and not isinstance(resp, BaseException):
            resp = resp(params)
        if isinstance(resp, BaseException):
            raise resp
        rows = list(resp or [])
        if rows:
            cols = list(rows[0].keys())
            self.description = [(c, "str", None, None, None, None, None) for c in cols]
            self._rows = [tuple(r.get(c) for c in cols) for r in rows]
        elif resp == [] and self.conn.empty_has_description:
            self.description = [("x", "str", None, None, None, None, None)]
            self._rows = []
        else:
            self.description = None
            self._rows = []
        self.rowcount = len(self._rows)

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows, self._rows = self._rows, []
        return rows

    def fetchmany(self, n: int) -> list[tuple[Any, ...]]:
        rows, self._rows = self._rows[:n], self._rows[n:]
        return rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows.pop(0) if self._rows else None

    def nextset(self) -> bool:
        return False

    def close(self) -> None:
        return None

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *a: object) -> None:
        return None


class FakeConnection:
    def __init__(self, routes: list[tuple[str | re.Pattern[str], Response]], *, empty_has_description: bool = True):
        self.routes = routes
        self.log: list[tuple[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.empty_has_description = empty_has_description

    def respond(self, sql: str, params: Any) -> Response:
        for key, resp in self.routes:
            if isinstance(key, re.Pattern):
                if key.search(sql):
                    return resp
            elif norm(key) in sql:
                return resp
        raise AssertionError(f"unexpected SQL issued by the adapter: {sql[:300]}")

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True

    def executed(self, fragment: str) -> bool:
        f = norm(fragment)
        return any(f in s for s, _ in self.log)


def attach(adapter: Any, conn: FakeConnection) -> FakeConnection:
    """Make a DB-API adapter use ``conn`` instead of opening a real connection."""
    adapter._open = lambda database: conn
    return conn
