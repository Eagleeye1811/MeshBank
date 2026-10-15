"""Small internal HTTP client for service-to-service calls.

* Base URLs come from configuration; nothing is hard-coded.
* Every call has an explicit timeout.
* Only calls marked ``retry_safe`` (reads, or writes carrying an idempotency
  key / operation ID) are retried.
* Failures are mapped to :class:`DownstreamError` with ``maybe_processed`` set
  when the remote side might have acted on the request (e.g. read timeout), so
  callers can distinguish "definitely not applied" from "outcome unknown".
"""

from __future__ import annotations

import logging
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from common.errors import DownstreamError
from common.logging_setup import REQUEST_ID_HEADER, get_request_id
from common.security import CALLER_SERVICE_HEADER, INTERNAL_TOKEN_HEADER

logger = logging.getLogger("meshbank.http_client")
ModelT = TypeVar("ModelT", bound=BaseModel)
_RETRYABLE_STATUS = {502, 503, 504}


class InternalClient:
    def __init__(
        self,
        *,
        target: str,
        base_url: str,
        caller: str,
        internal_token: str,
        timeout_seconds: float,
        max_attempts: int = 2,
        backoff_seconds: float = 0.1,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.target = target
        self.code_prefix = target.upper().replace("-", "_")
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            headers={INTERNAL_TOKEN_HEADER: internal_token, CALLER_SERVICE_HEADER: caller},
        )

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        retry_safe: bool = False,
    ) -> httpx.Response:
        attempts = self.max_attempts if retry_safe else 1
        maybe_processed = False
        last_error: DownstreamError | None = None
        for attempt in range(1, attempts + 1):
            headers = {}
            if request_id := get_request_id():
                headers[REQUEST_ID_HEADER] = request_id
            started = time.perf_counter()
            try:
                response = self._client.request(
                    method, path, json=json, params=params, headers=headers
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                last_error = self._error(
                    "unavailable", 503, maybe_processed, f"{type(exc).__name__}"
                )
            except httpx.TimeoutException as exc:
                maybe_processed = True
                last_error = self._error("timeout", 504, True, type(exc).__name__)
            except httpx.TransportError as exc:
                maybe_processed = True
                last_error = self._error("unavailable", 503, True, type(exc).__name__)
            else:
                elapsed = round((time.perf_counter() - started) * 1000, 2)
                logger.info(
                    "downstream call",
                    extra={
                        "target": self.target,
                        "method": method,
                        "path": path,
                        "status_code": response.status_code,
                        "duration_ms": elapsed,
                        "attempt": attempt,
                    },
                )
                if response.status_code < 500:
                    return response
                maybe_processed = True
                last_error = self._error("error", 502, True, f"HTTP {response.status_code}")
                if response.status_code not in _RETRYABLE_STATUS:
                    break
            if attempt < attempts:
                time.sleep(self.backoff_seconds * attempt)
        raise last_error or DownstreamError(f"The {self.target} is unavailable")

    def _error(self, kind: str, status: int, maybe_processed: bool, cause: str) -> DownstreamError:
        logger.warning(
            "downstream call failed",
            extra={"target": self.target, "failure": kind, "cause": cause},
        )
        messages = {
            "unavailable": f"The {self.target} is unavailable",
            "timeout": f"The {self.target} did not respond in time",
            "error": f"The {self.target} returned an error",
        }
        return DownstreamError(
            messages[kind],
            code=f"{self.code_prefix}_{kind.upper()}",
            status_code=status,
            maybe_processed=maybe_processed,
        )

    def parse(self, response: httpx.Response, model: type[ModelT]) -> ModelT:
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            logger.warning("invalid downstream response", extra={"target": self.target})
            raise DownstreamError(
                f"The {self.target} returned an invalid response",
                code=f"{self.code_prefix}_INVALID_RESPONSE",
                status_code=502,
                maybe_processed=True,
            ) from exc


def error_code_of(response: httpx.Response) -> str | None:
    """Extract ``error.code`` from a MeshBank error response, if present."""
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        code = body["error"].get("code")
        return code if isinstance(code, str) else None
    return None
