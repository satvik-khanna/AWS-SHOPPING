"""Payments: card (Stripe PaymentMethod token), stripe, paypal (Orders v2), venmo (via PayPal).

    from shopper.payments import build_provider
    p = build_provider("card")
    ref = p.methods("alice")[0]
    it = p.create_intent("alice", cart_total, ref.token, idem_key, cart_session_id=cart.session_id)
    ... user swipes -> checkout approval gate mints the ApprovalGrant for that cart ...
    it = p.confirm(it, grant)          # charges once; blocked without the grant or on amount mismatch

Offline fake by default. Live only with SHOPPER_LIVE=1 and test/sandbox credentials:
  card / stripe : STRIPE_SECRET_KEY (sk_test_ or rk_test_), optional STRIPE_CUSTOMER_ID, STRIPE_RETURN_URL
  paypal / venmo: PAYPAL_CLIENT_ID + PAYPAL_CLIENT_SECRET (sandbox app), PAYPAL_ENV=sandbox (default),
                  optional PAYPAL_CUSTOMER_ID (vaulted methods)
A live-mode key raises LiveKeyRefused; a missing key falls back to the fake.
"""
from __future__ import annotations

from ..config import data_dir, live
from .backends import FakeBackend, LiveKeyRefused, PayPalBackend, StripeBackend
from .core import GuardedProvider, Ledger

KINDS = ("card", "stripe", "paypal", "venmo")


def _backend(kind: str):
    if live():
        try:
            return StripeBackend(kind) if kind in ("card", "stripe") else PayPalBackend(kind)
        except LiveKeyRefused as e:
            if "not set" not in str(e):
                raise                       # a live-mode key was supplied: refuse loudly, never fall back silently
    return FakeBackend(kind)


def build_provider(kind: str, sessions=None, ledger: Ledger | None = None) -> GuardedProvider:
    if kind not in KINDS:
        raise ValueError(f"unknown payment kind {kind!r}; one of {KINDS}")
    if ledger is None:
        ledger = Ledger(data_dir() / "payments" / "ledger.json")
    return GuardedProvider(kind, _backend(kind), sessions=sessions, ledger=ledger)


__all__ = ["KINDS", "build_provider", "GuardedProvider", "Ledger", "FakeBackend", "StripeBackend",
           "PayPalBackend", "LiveKeyRefused"]
