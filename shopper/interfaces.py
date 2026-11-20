"""Module boundaries. One owner per protocol; implementations live in their own subpackage.

Rule for every implementation: no paid / live network call by default. Real adapters
(Senso, Superlinked, Cognee, Browserbase, Claude API) are opt-in via env var
SHOPPER_LIVE=1 and must fall back to the offline implementation when unset.
"""
from __future__ import annotations

from typing import Protocol

from .models import (CartResult, LandedCost, Listing, Preferences, RankedPick,
                     ShoppingRequest)


class ListingSource(Protocol):          # owner: shopper/listings
    def search(self, req: ShoppingRequest, prefs: Preferences) -> list[Listing]: ...


class MemoryStore(Protocol):            # owner: shopper/memory
    def recall(self, user_id: str) -> Preferences: ...
    def remember_purchase(self, user_id: str, listing: Listing) -> None: ...
    def reject_seller(self, user_id: str, seller: str) -> None: ...


class CostEngine(Protocol):             # owner: shopper/cost
    def landed_cost(self, listing: Listing, req: ShoppingRequest) -> LandedCost: ...


class Ranker(Protocol):                 # owner: shopper/rank
    def rank(self, req: ShoppingRequest, prefs: Preferences,
             candidates: list[tuple[Listing, LandedCost]]) -> list[RankedPick]: ...


class Checkout(Protocol):               # owner: shopper/checkout
    def fill_cart(self, pick: RankedPick) -> CartResult: ...
    """Must stop at status='awaiting_approval'. Never pays."""

    def approve(self, approval_token: str) -> CartResult: ...
    """Only path to 'placed'. Rejects unknown/used tokens."""

    def reject(self, approval_token: str, reason: str = "", reject_seller: bool = False) -> CartResult: ...
    """Reject a pending approval token and optionally block the seller."""
