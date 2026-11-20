"""Cart + approval gate. Nothing is ever paid without approve(token).

DryRunCheckout keeps its old name for app.py wiring; it is the BuyeeCheckout state machine
with the offline simulated Buyee driver (live Playwright driver only with SHOPPER_LIVE=1
and BUYEE_STORAGE_STATE set). See core.py / machine.py / drivers.py.
"""
from __future__ import annotations

from .core import BuyeeCheckout
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
        super().__init__(**kw)


__all__ = ["BuyeeCheckout", "DryRunCheckout", "SimBuyeeDriver", "LiveBuyeeDriver",
           "InMemorySessionStore", "FileSessionStore"]
