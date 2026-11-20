"""Sponsor integrations. Each adapter talks to the real service when
configured and exposes an explicit state so the UI and report never claim a
live integration that did not actually run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import now_iso


@dataclass
class IntegrationState:
    name: str
    label: str
    configured: bool = False
    fallback_label: str = "local fallback"
    unconfigured_detail: str = ""
    live_calls: int = 0
    fallback_calls: int = 0
    last_success_at: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def ok(self) -> None:
        self.live_calls += 1
        self.last_success_at = now_iso()
        self.last_error = None

    def fail(self, error: str) -> None:
        self.fallback_calls += 1
        self.last_error = error[:400]
        self.last_error_at = now_iso()

    def used_fallback(self) -> None:
        self.fallback_calls += 1

    @property
    def mode(self) -> str:
        """live                 - most recent call hit the real service and succeeded
        configured_untested  - credentials present, nothing called yet
        error                - configured, most recent call failed, fallback in use
        fallback             - not configured; local fallback in use"""
        if not self.configured:
            return "fallback"
        if self.last_error:
            return "error"
        if self.last_success_at:
            return "live"
        return "configured_untested"

    def to_json(self) -> dict[str, Any]:
        if not self.configured:
            detail = self.unconfigured_detail or f"Not configured; using {self.fallback_label}."
        elif self.last_error:
            detail = f"Last call failed, using {self.fallback_label}: {self.last_error}"
        elif self.last_success_at:
            detail = f"Live call succeeded at {self.last_success_at}."
        else:
            detail = "Configured; no call has been made yet in this process."
        return {
            "name": self.name, "label": self.label, "mode": self.mode, "configured": self.configured,
            "detail": detail, "fallbackLabel": self.fallback_label, "liveCalls": self.live_calls,
            "fallbackCalls": self.fallback_calls, "lastSuccessAt": self.last_success_at,
            "lastError": self.last_error, "lastErrorAt": self.last_error_at, **self.extra,
        }
