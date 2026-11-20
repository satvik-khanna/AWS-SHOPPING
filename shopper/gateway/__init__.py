"""Gateway package: 1 URL -> Human Link-in-bio + MCP + UCP + ACP + AI Discovery."""
from __future__ import annotations

from .interfaces import ProtocolAdapter, StoreRegistry
from .models import CheckoutSession, StoreProfile
from .protocols import UnifiedCommerceAdapter
from .server import GatewayHTTPServer, make_server
from .store import JsonStoreRegistry

__all__ = [
    "CheckoutSession",
    "GatewayHTTPServer",
    "JsonStoreRegistry",
    "ProtocolAdapter",
    "StoreProfile",
    "StoreRegistry",
    "UnifiedCommerceAdapter",
    "make_server",
]
