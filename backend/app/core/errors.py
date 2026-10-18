"""Application error hierarchy and consistent JSON error responses."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_ctx

log = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code


class NotAuthenticated(AppError):
    status_code = 401
    code = "not_authenticated"


class PermissionDenied(AppError):
    status_code = 403
    code = "permission_denied"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class ConfirmationRequired(AppError):
    """Raised when an operation needs an explicit, typed confirmation from the user."""

    status_code = 428
    code = "confirmation_required"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class CapabilityNotSupported(AppError):
    status_code = 422
    code = "capability_not_supported"


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"


class TargetUnavailable(AppError):
    """The managed (target) database could not be reached or returned an error."""

    status_code = 502
    code = "target_unavailable"


class MonitoringStorageUnavailable(AppError):
    """The time-series database (monitoring telemetry storage) is unreachable."""

    status_code = 503
    code = "timeseries_unavailable"


def _body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id_ctx.get(),
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details))

    from app.timeseries.repository import TimeSeriesError, TimeSeriesUnavailable

    @app.exception_handler(TimeSeriesUnavailable)
    async def _ts_unavailable(_: Request, exc: TimeSeriesUnavailable) -> JSONResponse:
        log.warning("time-series storage unavailable", extra={"error": str(exc)})
        return JSONResponse(
            status_code=503,
            content=_body(
                "timeseries_unavailable",
                "Monitoring storage is unavailable. Inventory and configuration remain usable; "
                "charts will return when the time-series database is reachable again.",
            ),
        )

    @app.exception_handler(TimeSeriesError)
    async def _ts_error(_: Request, exc: TimeSeriesError) -> JSONResponse:
        log.warning("time-series query rejected", extra={"error": str(exc)})
        return JSONResponse(
            status_code=502, content=_body("timeseries_error", "The monitoring storage rejected the query")
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content=_body("validation_failed", "Invalid request", {"errors": errors}))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_body("http_error", str(exc.detail)))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return JSONResponse(status_code=500, content=_body("internal_error", "An unexpected error occurred"))
