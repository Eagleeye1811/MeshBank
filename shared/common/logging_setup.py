"""Structured JSON logging with request-ID correlation.

Never log passwords, tokens or Authorization headers: the request middleware
only records method, path, status and duration.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from datetime import UTC, datetime

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

REQUEST_ID_HEADER = "X-Request-ID"
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message"}


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(value: str | None) -> contextvars.Token:
    return _request_id.set(value)


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service_name: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Uvicorn's access log would duplicate our request log; keep its error log.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _sanitize_request_id(raw: str | None) -> str:
    if raw and 0 < len(raw) <= 128 and all(c.isalnum() or c in "-_.:" for c in raw):
        return raw
    return str(uuid.uuid4())


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request ID, echoes it back and logs request timing."""

    def __init__(self, app, logger_name: str = "meshbank.http") -> None:
        super().__init__(app)
        self.logger = logging.getLogger(logger_name)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _sanitize_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = set_request_id(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Handled here (rather than by Starlette's outermost handler) so
                # the client still receives a request ID and a safe JSON body.
                self.logger.exception("unhandled error")
                response = JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "An unexpected error occurred",
                            "request_id": request_id,
                        }
                    },
                )
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            self.logger.info(
                "request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "caller": request.headers.get("X-Caller-Service", "external"),
                },
            )
            _request_id.reset(token)
