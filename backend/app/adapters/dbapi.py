"""Common machinery for adapters built on blocking DB-API 2.0 drivers.

PyMySQL (MySQL/MariaDB), pyodbc (SQL Server), python-oracledb (Oracle) and ibm_db_dbi (Db2)
are synchronous. Calls run on one dedicated worker thread per adapter instance, so

* the event loop (API, scheduler, other collectors) is never blocked,
* a driver connection is only ever used from a single thread (several of these drivers are
  not thread-safe per connection), and
* every call has two timeouts: the driver's own statement/call timeout, configured per
  connection by the engine adapter, and an asyncio deadline as a safety net. When the
  deadline fires, the worker thread and its connections are abandoned and a fresh thread is
  used for the next call, so a hung Oracle/Db2/SQL Server socket cannot stall the pipeline.

Engine adapters implement :meth:`_open` (create and configure a connection) and
:meth:`_classify` (translate driver exceptions into normalized :class:`AdapterError` codes).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import ssl
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar, TypeVar
from uuid import UUID

from app.adapters.base import (
    AdapterConnectionError,
    AdapterError,
    AdapterErrorCode,
    AdapterNotSupported,
    AdapterPermissionError,
    AdapterTimeout,
    AdapterTLSError,
    BaseDatabaseAdapter,
    Capability,
    ConnectionSpec,
    ExecutionResult,
)

log = logging.getLogger(__name__)
T = TypeVar("T")
Row = dict[str, Any]


class AdapterObjectMissing(AdapterError):
    """A catalog view, table or facility the query needs does not exist on this server
    (older version, edition, feature disabled). Optional facilities treat it as "absent"."""

    code = AdapterErrorCode.FEATURE_NOT_AVAILABLE


def jsonable(v: Any) -> Any:
    """Driver values → JSON-safe values (UTC for timestamps; naive values are treated as UTC
    because every adapter pins its session to UTC or converts in SQL)."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 2**53 else float(v)
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=UTC)).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, timedelta):
        return v.total_seconds()
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return "0x" + b.hex()[:2048]
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if hasattr(v, "read"):  # LOB locators (Oracle CLOB, Db2 BLOB)
        try:
            return jsonable(v.read())
        except Exception:  # noqa: BLE001
            return None
    return str(v)


def num(v: Any, default: float | None = None) -> float | None:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def utc(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v)
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return None


def text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace")
    return str(v).strip() if isinstance(v, str) else str(v)


class SyncDBAPIAdapter(BaseDatabaseAdapter):
    """Base class for engines whose Python driver is a blocking DB-API module."""

    #: open one connection per database name (False: a single connection, database selected in SQL)
    connection_per_database: ClassVar[bool] = False
    #: placeholders the driver uses ("%s" PyMySQL, "?" pyodbc/ibm_db, ":1" oracledb)
    paramstyle: ClassVar[str] = "?"
    #: pass an empty parameter tuple when there are none (PyMySQL then still turns "%%" into "%")
    always_bind: ClassVar[bool] = False

    def __init__(self, spec: ConnectionSpec):
        super().__init__(spec)
        self._conns: dict[str, Any] = {}
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._memo: dict[str, Any] = {}

    # ------------------------------------------------------------------------------------
    # engine hooks
    # ------------------------------------------------------------------------------------
    def _open(self, database: str | None) -> Any:  # pragma: no cover - engine specific
        """Create a configured connection (timeouts, session settings). Runs in the worker thread."""
        raise NotImplementedError

    def _classify(self, exc: BaseException) -> AdapterError:
        """Map a driver exception to a normalized adapter error (never include credentials)."""
        return AdapterError(_clean(str(exc)))

    def _begin_read_only(self, cursor: Any) -> None:
        """Start a read-only transaction when the engine supports it (workspace / EXPLAIN)."""

    def _set_call_timeout(self, conn: Any, seconds: float) -> None:
        """Adjust the per-statement timeout of a connection (workspace runs with its own limit)."""

    def _type_name(self, type_code: Any) -> str:
        return getattr(type_code, "__name__", None) or str(type_code)

    # ------------------------------------------------------------------------------------
    # thread + connection management
    # ------------------------------------------------------------------------------------
    @property
    def _key_default(self) -> str:
        return self.spec.database or ""

    def _pool(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"dbm-{self.engine}")
        return self._executor

    def _deadline(self, timeout: float | None) -> float:
        return (timeout if timeout is not None else self.spec.statement_timeout) + self.spec.connect_timeout + 5

    async def _run(self, fn: Callable[..., T], *args: Any, timeout: float | None = None) -> T:  # noqa: ASYNC109
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self._pool(), functools.partial(fn, *args))
        try:
            return await asyncio.wait_for(fut, timeout=self._deadline(timeout))
        except TimeoutError as exc:
            self._abandon()
            raise AdapterTimeout(
                f"{self.display_name} did not answer within {self._deadline(timeout):.0f}s; the call was abandoned"
            ) from exc
        except AdapterError:
            raise
        except OSError as exc:  # DNS failure, refused/reset socket raised by the driver itself
            msg = clean_message(str(exc))
            if isinstance(exc, ssl.SSLError):
                raise AdapterTLSError(f"TLS negotiation failed: {msg}") from exc
            raise AdapterConnectionError(f"cannot connect to {self.spec.host}:{self.spec.port}: {msg}") from exc
        except Exception as exc:  # noqa: BLE001 - every driver error is normalized
            err = self._classify(exc)
            if err is exc:
                raise
            raise err from exc

    def _abandon(self) -> None:
        """Forget a worker that is stuck inside the driver; its connections are closed by the
        driver when the socket finally fails, and never reused."""
        conns, self._conns = self._conns, {}
        ex, self._executor = self._executor, None
        if ex is not None:
            ex.shutdown(wait=False, cancel_futures=True)
        log.warning("abandoned a hung %s connection (%d open)", self.engine, len(conns))

    def _get_conn(self, database: str | None) -> Any:
        key = (database or self._key_default) if self.connection_per_database else ""
        conn = self._conns.get(key)
        if conn is None:
            conn = self._open(database if self.connection_per_database else self.spec.database)
            self._conns[key] = conn
        return conn

    async def connect(self) -> None:
        await self._run(self._get_conn, None)

    async def close(self) -> None:
        if not self._conns and self._executor is None:
            return
        conns, self._conns = list(self._conns.values()), {}

        def _close_all() -> None:
            for c in conns:
                try:
                    c.close()
                except Exception:  # noqa: BLE001, S110 - best effort
                    pass

        ex, self._executor = self._executor, None
        if ex is not None:
            try:
                await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(ex, _close_all), timeout=5)
            except Exception:  # noqa: BLE001, S110
                pass
            ex.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------------------------
    def _query_sync(
        self, sql: str, params: Sequence[Any] | dict[str, Any] | None, database: str | None, max_rows: int | None
    ) -> list[Row]:
        conn = self._get_conn(database)
        cur = conn.cursor()
        try:
            if params:
                cur.execute(sql, params)
            elif self.always_bind:
                cur.execute(sql, ())
            else:
                cur.execute(sql)
            if not cur.description:
                return []
            cols = [str(d[0]).lower() for d in cur.description]
            raw = cur.fetchmany(max_rows) if max_rows else cur.fetchall()
            return [dict(zip(cols, r, strict=False)) for r in raw]
        finally:
            try:
                cur.close()
            except Exception:  # noqa: BLE001, S110
                pass

    async def _query(
        self,
        sql: str,
        params: Sequence[Any] | dict[str, Any] | None = None,
        *,
        database: str | None = None,
        max_rows: int | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> list[Row]:
        return await self._run(self._query_sync, sql, params, database, max_rows, timeout=timeout)

    async def _one(self, sql: str, params: Any = None, *, database: str | None = None) -> Row | None:
        rows = await self._query(sql, params, database=database, max_rows=1)
        return rows[0] if rows else None

    async def _try(self, sql: str, params: Any = None, *, database: str | None = None) -> list[Row] | None:
        """Optional facility: ``None`` when the account lacks the privilege or the object does
        not exist on this version/edition; other errors propagate."""
        try:
            return await self._query(sql, params, database=database)
        except (AdapterPermissionError, AdapterObjectMissing, AdapterNotSupported):
            return None

    async def _try_one(self, sql: str, params: Any = None, *, database: str | None = None) -> Row | None:
        rows = await self._try(sql, params, database=database)
        return rows[0] if rows else None

    async def _memoized(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self._memo:
            self._memo[key] = await fn()
        return self._memo[key]

    # ------------------------------------------------------------------------------------
    # workspace
    # ------------------------------------------------------------------------------------
    def _execute_sync(
        self, sql: str, database: str | None, read_only: bool, max_rows: int, timeout_s: float
    ) -> ExecutionResult:
        conn = self._get_conn(database)
        self._set_call_timeout(conn, timeout_s)
        start = time.perf_counter()
        result = ExecutionResult()
        cur = conn.cursor()
        ok = False
        try:
            if read_only:
                self._begin_read_only(cur)
            cur.execute(sql)
            if cur.description:
                result.columns = [{"name": str(d[0]), "type": self._type_name(d[1])} for d in cur.description]
                rows = cur.fetchmany(max_rows + 1)
                result.truncated = len(rows) > max_rows
                result.rows = [[jsonable(v) for v in r] for r in rows[:max_rows]]
                result.row_count = len(result.rows)
                result.status_message = f"{result.row_count}{'+' if result.truncated else ''} row(s)"
            else:
                rc = getattr(cur, "rowcount", -1)
                result.row_count = max(int(rc or 0), 0)
                result.status_message = f"{result.row_count} row(s) affected" if rc is not None and rc >= 0 else "OK"
            ok = True
        finally:
            try:
                if ok and not read_only:
                    conn.commit()
                    result.committed = True
                else:
                    conn.rollback()
            finally:
                try:
                    cur.close()
                except Exception:  # noqa: BLE001, S110
                    pass
                self._set_call_timeout(conn, self.spec.statement_timeout)
        result.duration_ms = round((time.perf_counter() - start) * 1000, 2)
        return result

    async def execute_sql(
        self, sql: str, database: str | None, *, read_only: bool, max_rows: int, timeout_s: float
    ) -> ExecutionResult:
        stmt = sql.strip().rstrip(";").strip()
        if not stmt:
            raise AdapterError("empty statement")
        return await self._run(self._execute_sync, stmt, database, read_only, max_rows, timeout_s, timeout=timeout_s)

    # ------------------------------------------------------------------------------------
    # administration
    # ------------------------------------------------------------------------------------
    def _admin_sync(self, stmt: Any) -> dict[str, Any]:
        dbs = stmt.statement_databases or [None] * len(stmt.statements)
        if len({d or stmt.database for d in dbs}) > 1:
            done = 0
            for sql, params, db in zip(stmt.statements, stmt.params, dbs, strict=True):
                sub = type(stmt)(stmt.action, stmt.risk, [sql], [params], [sql], database=db or stmt.database)
                done += self._admin_sync(sub)["statements_executed"]
            return {"statements_executed": done}
        conn = self._get_conn(dbs[0] or stmt.database)
        cur = conn.cursor()
        done = 0
        try:
            for sql, params in zip(stmt.statements, stmt.params, strict=True):
                if params:
                    cur.execute(sql, params)
                elif self.always_bind:
                    cur.execute(sql, ())
                else:
                    cur.execute(sql)
                done += 1
            conn.commit()
        except BaseException:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001, S110
                pass
            raise
        finally:
            try:
                cur.close()
            except Exception:  # noqa: BLE001, S110
                pass
        return {"statements_executed": done}

    async def run_admin(self, stmt: Any) -> dict[str, Any]:
        return await self._run(self._admin_sync, stmt)

    def _not_supported(self, c: Capability, reason: str) -> AdapterNotSupported:
        return AdapterNotSupported(c, reason)


_SECRET_RE = re.compile(r"(?i)(pwd|password|passwd)\s*=\s*[^;\s]+")


def _clean(msg: str) -> str:
    """Strip anything that looks like a credential from driver messages."""
    return _SECRET_RE.sub(r"\1=***", msg)[:1000]


clean_message = _clean
