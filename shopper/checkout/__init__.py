"""Cart + approval gate. Base: dry run, never touches a real site, never pays.
TODO(checkout pane): Buyee cart fill via Stagehand/Browser Use on Browserbase behind SHOPPER_LIVE,
Firedrill-style simulated runs proving it stops at approval."""
from __future__ import annotations

import secrets

from ..models import CartResult, RankedPick


class DryRunCheckout:
    def __init__(self):
        self._pending: dict[str, str] = {}   # token -> listing_id

    def fill_cart(self, pick: RankedPick) -> CartResult:
        token = secrets.token_urlsafe(8)
        self._pending[token] = pick.listing.id
        return CartResult(status="awaiting_approval", listing_id=pick.listing.id, approval_token=token,
                          cart_url=None, detail=f"dry run: would add {pick.listing.url} to Buyee cart")

    def approve(self, approval_token: str) -> CartResult:
        listing_id = self._pending.pop(approval_token, None)
        if listing_id is None:
            return CartResult(status="blocked", listing_id="", detail="unknown or already used approval token")
        return CartResult(status="placed", listing_id=listing_id, detail="dry run: order not actually placed")
