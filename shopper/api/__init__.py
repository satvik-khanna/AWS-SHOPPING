"""Stdlib-only HTTP JSON API for the frontend. Start: python3 -m shopper.api --port 8000. Contract: docs/API.md."""
from .server import ApiError, Service, make_server

__all__ = ["ApiError", "Service", "make_server"]
