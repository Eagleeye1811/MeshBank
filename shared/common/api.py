"""Small API helpers: pagination and idempotency fingerprints."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int


def page_params(
    limit: int = Query(20, ge=1, le=100, description="Maximum number of items to return"),
    offset: int = Query(0, ge=0, le=1_000_000, description="Number of items to skip"),
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


def fingerprint(payload: dict[str, Any]) -> str:
    """Stable SHA-256 of a request payload, used to bind idempotency keys."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_.:\-]{8,128}$"
