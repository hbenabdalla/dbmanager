"""Application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.ops import router as ops_router
from app.api.v1 import api_router
from app.core.config import SETUP_HINT, get_settings, placeholder_settings
from app.core.crypto import get_keyring
from app.core.errors import PermissionDenied, install_error_handlers
from app.core.logging import configure_logging, request_id_ctx, user_ctx
from app.core.middleware import RequestContextMiddleware, SecureHeadersMiddleware
from app.db.session import dispose_engine, session_scope

log = logging.getLogger("app")


#: serialises the start-up work (migrations, bootstrap, data moves) of replicas starting together
STARTUP_LOCK_ID = 0x44424D_535455  # "DBM" "STU"


async def startup_tasks() -> None:
    s = get_settings()
    if placeholders := placeholder_settings(s):
        raise RuntimeError(
            f"Configuration still contains CHANGE_ME placeholders: {', '.join(placeholders)}. {SETUP_HINT}"
        )
    get_keyring()  # fail fast when the master key is missing or malformed
    from sqlalchemy import text

    from app.db.session import get_engine

    # one replica at a time: the others wait here and then find nothing left to do (bootstrap
    # and the data moves are idempotent); the lock ends with the connection if a process dies
    async with get_engine().connect() as lock:
        await lock.execute(text("SELECT pg_advisory_lock(:k)"), {"k": STARTUP_LOCK_ID})
        await lock.commit()
        try:
            await _startup_work(s)
        finally:
            await lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": STARTUP_LOCK_ID})
            await lock.commit()


async def _startup_work(s) -> None:  # type: ignore[no-untyped-def]
    if s.run_migrations_on_start:
        from app.cli import run_migrations

        await asyncio.to_thread(run_migrations)
    from app.seed.bootstrap import bootstrap

    async with session_scope() as db:
        await bootstrap(db)
    from app.services import notify

    async with session_scope() as db:
        # webhook URLs and headers stored in plain text by older versions move into the secret
        moved = await notify.migrate_plaintext_webhooks(db)
        if moved:
            log.info("moved %d webhook URL(s) into encrypted storage", moved)
    if s.demo_mode:
        # No simulated data is ever created at startup any more.
        log.warning(
            "DBM_DEMO_MODE is deprecated and ignored: demo data is no longer generated at startup. "
            "A Platform Administrator can generate it under Platform -> Simulation."
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    await startup_tasks()
    from app.jobs import runner as job_runner
    from app.timeseries.repository import get_repository
    from app.timeseries.writer import get_writer

    get_writer().start()  # batches telemetry to the time-series DB; never blocks collectors
    if s.timeseries_enabled and not await get_repository().health_check():
        log.warning(
            "time-series database is not reachable; monitoring telemetry is buffered and retried",
            extra={"url": s.timeseries_url},
        )
    if s.runs_worker:
        job_runner.runner = job_runner.BackgroundRunner()
        job_runner.runner.start()
    from app.services import instances

    instances.heartbeat = instances.Heartbeat()
    instances.heartbeat.start()
    log.info("DBManager started", extra={"version": __version__, "role": s.process_role, "env": s.env})
    try:
        yield
    finally:
        if instances.heartbeat:
            await instances.heartbeat.stop()
            instances.heartbeat = None
        if job_runner.runner:
            await job_runner.runner.stop()
            job_runner.runner = None
        await get_writer().stop()  # final flush (best effort)
        await get_repository().close()
        await dispose_engine()


def create_app(*, with_lifespan: bool = True) -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="DBManager API",
        version=__version__,
        description=(
            "Unified administration, monitoring, security and performance analysis for heterogeneous relational "
            "databases. Authenticate with `POST /api/v1/auth/login`; unsafe methods require the `X-CSRF-Token` "
            "header (value of the `dbm_csrf` cookie)."
        ),
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan if with_lifespan else None,
    )
    install_error_handlers(app)

    @app.exception_handler(PermissionDenied)
    async def _denied(request: Request, exc: PermissionDenied) -> JSONResponse:
        from app.services import audit

        if request.method not in ("GET", "HEAD", "OPTIONS") or exc.details.get("permission"):
            await audit.record(
                "access.denied",
                result="denied",
                username=user_ctx.get(),
                category="security",
                failure_reason=exc.message,
                details={"method": request.method, "path": request.url.path, **exc.details},
            )
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "request_id": request_id_ctx.get(),
                }
            },
        )

    app.add_middleware(SecureHeadersMiddleware)
    if s.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=s.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID", "Authorization"],
        )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(ops_router)
    app.include_router(api_router)
    return app


app = create_app()
