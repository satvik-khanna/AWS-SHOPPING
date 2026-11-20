"""Gateway package: 1 URL -> Human Link-in-bio + MCP + UCP + ACP + AI Discovery."""
from __future__ import annotations

from .interfaces import ProtocolAdapter, StoreRegistry
from .models import CheckoutSession, StoreProfile

__all__ = [
    "CheckoutSession",
    "ProtocolAdapter",
    "StoreProfile",
    "StoreRegistry",
]
