"""Consistent error model and FastAPI exception handlers.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": ...}}

``details`` is present only when useful (for example, validation errors or the
ID of a transaction that failed because of a downstream outage).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from common.logging_setup import get_request_id

logger = logging.getLogger("meshbank.errors")


class ServiceError(Exception):
    """A controlled, client-safe error."""

    status_code = 400
    code = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.headers = headers
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details


class BadRequestError(ServiceError):
    status_code = 400
    code = "BAD_REQUEST"


class UnauthorizedError(ServiceError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(ServiceError):
    status_code = 403
    code = "FORBIDDEN"


class NotFoundError(ServiceError):
    status_code = 404
    code = "NOT_FOUND"


class ConflictError(ServiceError):
    status_code = 409
    code = "CONFLICT"


class TooManyRequestsError(ServiceError):
    status_code = 429
    code = "TOO_MANY_REQUESTS"


class UnprocessableError(ServiceError):
    status_code = 422
    code = "UNPROCESSABLE"


class DownstreamError(ServiceError):
    """A dependency failed. ``maybe_processed`` tells callers whether the
    remote side may have acted on the request (for example after a timeout)."""

    status_code = 503
    code = "DOWNSTREAM_UNAVAILABLE"

    def __init__(self, message: str, *, maybe_processed: bool = False, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.maybe_processed = maybe_processed


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message, "request_id": get_request_id()}
    if details is not None:
        body["details"] = details
    return {"error": body}


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Echoing rejected input back could leak secrets (e.g. passwords), so only
    # location, message and type are returned.
    return [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in errors
    ]


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
        log = logger.warning if exc.status_code >= 500 else logger.info
        log("service error", extra={"error_code": exc.code, "status_code": exc.status_code})
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR",
                "The request is invalid",
                _sanitize_validation_errors(exc.errors()),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "UNAUTHORIZED"}
        code = codes.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(
            status_code=exc.status_code, content=error_body(code, message), headers=exc.headers
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=500, content=error_body("INTERNAL_ERROR", "An unexpected error occurred")
        )
