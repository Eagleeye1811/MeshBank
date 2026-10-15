"""Shared MeshBank utilities.

This package contains cross-cutting concerns only (configuration, logging,
errors, JWT verification, persistence helpers, money handling and an internal
HTTP client). It deliberately contains no business logic belonging to any
single service, so services stay independently deployable.
"""
