"""Data types for the 1-URL multi-protocol commerce gateway (MCP + UCP + ACP + Discovery)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StoreProfile:
    """A creator/merchant storefront registered from a bio/shop link."""
    slug: str                           # URL-safe handle, e.g. "tokyo-camera"
    name: str                           # Display name
    source_url: str                     # Original Linktree / Instagram / Mercari / shop URL
    bio: str = ""
    ship_from: str = "JP"
    default_ship_to: str = "US-CA"
    query_filter: str = ""              # Optional query filter into ListingSource
    tags: list[str] = field(default_factory=list)


@dataclass
class CheckoutSession:
    """Protocol-agnostic checkout session mapped to MCP, UCP, and ACP."""
    id: str
    store_slug: str
    listing_id: str
    title: str
    quantity: int
    ship_to: str
    currency: str                       # ISO 4217 uppercase ("USD"); ACP lowercases it
    subtotal_usd: float
    shipping_usd: float
    tax_duty_usd: float
    total_usd: float
    status: str                         # "awaiting_approval" | "completed" | "canceled"
    approval_token: str | None = None   # Single-use token from Checkout.fill_cart
    cart_url: str | None = None
    buyer: dict[str, str] = field(default_factory=dict)
    citations: list[dict[str, str]] = field(default_factory=list)
    order_id: str | None = None
