"""Activity log: every notable step is persisted so the UI can show a
source-to-decision timeline and the report reflects what actually happened."""

from __future__ import annotations

import logging
from typing import Any

from .store import Store

logger = logging.getLogger("kai")


class ActivityLog:
    def __init__(self, store: Store):
        self.store = store

    def log(self, stage: str, message: str, status: str = "info", run_id: str | None = None,
            data: dict[str, Any] | None = None) -> dict[str, Any]:
        level = {"error": logging.ERROR, "warn": logging.WARNING}.get(status, logging.INFO)
        logger.log(level, "[%s] %s", stage, message)
        return self.store.add_event(stage, message, status, run_id, data)

    def usage(self, service: str, operation: str, units: float, unit_name: str,
              cost_usd: float | None, meta: dict[str, Any] | None = None) -> None:
        self.store.add_usage(service, operation, units, unit_name, cost_usd, meta)
