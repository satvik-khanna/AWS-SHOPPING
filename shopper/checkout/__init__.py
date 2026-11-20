"""Cart + approval gate. Nothing is ever paid without approve(token).

DryRunCheckout keeps its old name for app.py wiring; it is the BuyeeCheckout state machine
with the offline FakeChromeDriver (full flow up to the payment step, state awaiting_swipe).
With SHOPPER_LIVE=1 the don-tacos dt-ax Chrome driver (or the Playwright Buyee cart) is used.
Per-user shipping / sign-in / payment references come from shopper.vault (JsonVault, 0600).
See core.py / machine.py / chrome.py / don_tacos_cua.py.
"""
from __future__ import annotations

from .core import BuyeeCheckout
from .chrome import FakeChromeDriver
from .drivers import LiveBuyeeDriver, SimBuyeeDriver
from .store import FileSessionStore, InMemorySessionStore


class DryRunCheckout(BuyeeCheckout):
    """Default wiring: sessions/tokens on disk (so `python3 -m shopper approve <token>` works
    in a later process) and approve/reject feeding the JSON memory."""

    def __init__(self, **kw):
        from ..config import data_dir
        from ..memory import build_memory
        kw.setdefault("store", FileSessionStore(data_dir() / "checkout"))
        kw.setdefault("memory", build_memory())
        if "vault" not in kw:
            from ..vault import JsonVault
            kw["vault"] = JsonVault()
        super().__init__(**kw)


__all__ = ["BuyeeCheckout", "DryRunCheckout", "SimBuyeeDriver", "LiveBuyeeDriver", "FakeChromeDriver",
           "InMemorySessionStore", "FileSessionStore"]
