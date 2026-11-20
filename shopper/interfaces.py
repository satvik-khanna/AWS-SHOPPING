"""Module boundaries. One owner per protocol; implementations live in their own subpackage.

Rule for every implementation: no paid / live network call by default. Real adapters
(Senso, Superlinked, Cognee, Browserbase, Claude API) are opt-in via env var
SHOPPER_LIVE=1 and must fall back to the offline implementation when unset.
"""
from __future__ import annotations

from typing import Protocol

from .models import (CartResult, LandedCost, Listing, PaymentIntent, PaymentMethodRef, Preferences,
                     RankedPick, ShippingAddress, ShoppingRequest, VaultRecord)


class ListingSource(Protocol):          # owner: shopper/listings
    def search(self, req: ShoppingRequest, prefs: Preferences) -> list[Listing]: ...


class MemoryStore(Protocol):            # owner: shopper/memory
    def recall(self, user_id: str) -> Preferences: ...
    def remember_purchase(self, user_id: str, listing: Listing) -> None: ...
    def reject_seller(self, user_id: str, seller: str) -> None: ...


class Vault(Protocol):                  # owner: shopper/vault
    """Per-user checkout info so the user does not re-enter it. Never holds passwords, card
    numbers or CVV: sign-in lives in the user's own Chrome profile (the vault only remembers
    which sites it is signed into) and cards are provider references (token, brand, last4, exp)."""
    def get(self, user_id: str) -> VaultRecord: ...
    def set_address(self, user_id: str, address: ShippingAddress) -> None: ...
    def set_signed_in(self, user_id: str, site: str, signed_in: bool) -> None: ...
    def add_payment_method(self, user_id: str, pm: PaymentMethodRef, default: bool = True) -> None: ...
    def remove_payment_method(self, user_id: str, token: str) -> None: ...


class CostEngine(Protocol):             # owner: shopper/cost
    def landed_cost(self, listing: Listing, req: ShoppingRequest) -> LandedCost: ...


class Ranker(Protocol):                 # owner: shopper/rank
    def rank(self, req: ShoppingRequest, prefs: Preferences,
             candidates: list[tuple[Listing, LandedCost]]) -> list[RankedPick]: ...


class Checkout(Protocol):               # owner: shopper/checkout
    def fill_cart(self, pick: RankedPick) -> CartResult: ...
    """Must stop at status='awaiting_approval'. Never pays."""

    def approve(self, approval_token: str) -> CartResult: ...
    """Only path to 'placed' (the user's swipe). Rejects unknown/used/expired tokens.
    Implementations may take `user_id=` and must then refuse a token issued to another user."""

    def reject(self, approval_token: str, reason: str = "", reject_seller: bool = False) -> CartResult: ...
    """Reject a pending approval token and optionally block the seller."""


class PaymentProvider(Protocol):        # owner: shopper/payments
    """card (Stripe PaymentMethod token) | stripe | paypal (Orders v2) | venmo (via PayPal).
    Offline fake by default; live sandbox/test keys only with SHOPPER_LIVE=1, live-mode keys refused."""

    def methods(self, user_id: str) -> list[PaymentMethodRef]: ...
    """Saved method references for the user (token + brand + last4 + exp; never a card number)."""

    def create_intent(self, user_id: str, amount_usd: float, method_ref: str, idempotency_key: str, *,
                      cart_session_id: str) -> PaymentIntent: ...
    """Same idempotency_key -> same intent, never a second one. Nothing is charged here."""

    def confirm(self, intent: PaymentIntent, grant: object) -> PaymentIntent: ...
    """Charges only with the checkout ApprovalGrant minted by the user's swipe for intent.cart_session_id,
    and only if intent.amount_usd equals that approved cart's landed total. Otherwise status='blocked'.
    A second confirm (double swipe) returns the first result without charging again."""

    def cancel(self, intent: PaymentIntent) -> PaymentIntent: ...
