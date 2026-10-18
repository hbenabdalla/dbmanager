"""Stand-alone collector worker: ``python -m app.worker``.

Equivalent to running the backend with ``DBM_PROCESS_ROLE=worker`` but without the HTTP
API (only /healthz, /readyz and /metrics on DBM_WORKER_PORT, default 8001, for probes).
"""

from __future__ import annotations

import asyncio
import os
import signal

import uvicorn
from fastapi import FastAPI

from app.api.ops import router as ops_router
from app.core.config import get_settings
from app.core.logging import configure_logging


async def main() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    from app.jobs.runner import BackgroundRunner
    from app.main import startup_tasks

    await startup_tasks()
    from app.jobs import runner as job_runner
    from app.timeseries.repository import get_repository
    from app.timeseries.writer import get_writer

    get_writer().start()  # collectors hand samples to the batch writer → time-series DB
    job_runner.runner = BackgroundRunner()
    job_runner.runner.start()
    probe = FastAPI(title="DB Manager worker", docs_url=None, redoc_url=None, openapi_url=None)
    probe.include_router(ops_router)
    server = uvicorn.Server(
        uvicorn.Config(
            probe,
            host="0.0.0.0",  # noqa: S104 - probe endpoint inside the container
            port=int(os.environ.get("DBM_WORKER_PORT", "8001")),
            log_level="warning",
        )
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    serve = asyncio.create_task(server.serve())
    await stop.wait()
    server.should_exit = True
    await job_runner.runner.stop()
    await get_writer().stop()  # final flush
    await get_repository().close()
    await serve


if __name__ == "__main__":
    asyncio.run(main())
