"""Cart drivers. A driver can open a listing, add it to a Buyee cart and read the cart back.

No driver has a pay method for the agent path: `place()` is only reachable through
BuyeeCheckout.approve() with an ApprovalGrant, and the live driver refuses to pay at all
(it hands the filled cart URL to the human).
"""
from __future__ import annotations

import os
import re
import urllib.parse

from .machine import ApprovalGrant

BUYEE_ITEM = {
    "mercari": "https://buyee.jp/mercari/item/{nid}",
    "yahoo_auctions": "https://buyee.jp/item/jdirectitems/auction/{nid}",
}
BUYEE_CART = "https://buyee.jp/cart"


def buyee_url(listing: dict) -> str:
    src = listing.get("source", "other")
    nid = listing.get("id", "").split(":", 1)[-1]
    tmpl = BUYEE_ITEM.get(src)
    if tmpl and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", nid):
        return tmpl.format(nid=nid)
    # unknown source: Buyee's "paste a URL" shopping service
    return "https://buyee.jp/item/search/url?" + urllib.parse.urlencode({"url": listing.get("url", "")})


class SimBuyeeDriver:
    """Offline simulation of Buyee. Deterministic, no network."""
    name = "sim-buyee"

    def __init__(self, fail_at: str | None = None):
        self.fail_at = fail_at          # firedrill: "open" | "add" | "read" to simulate site errors
        self.carts: dict[str, list[dict]] = {}
        self.orders: list[dict] = []

    def _maybe_fail(self, step: str) -> None:
        if self.fail_at == step:
            raise RuntimeError(f"simulated Buyee error at {step}")

    def open_listing(self, session_id: str, listing: dict) -> str:
        self._maybe_fail("open")
        return buyee_url(listing)

    def add_to_cart(self, session_id: str, listing: dict) -> None:
        self._maybe_fail("add")
        self.carts.setdefault(session_id, []).append(
            {"listing_id": listing.get("id"), "price_jpy": listing.get("price_jpy"), "qty": 1})

    def read_cart(self, session_id: str) -> tuple[list[dict], str]:
        self._maybe_fail("read")
        return list(self.carts.get(session_id, [])), f"{BUYEE_CART}#sim-{session_id[:8]}"

    def place(self, session_id: str, grant: ApprovalGrant) -> str:
        if not isinstance(grant, ApprovalGrant) or grant.session_id != session_id or grant.used:
            raise PermissionError("place() needs an unused approval grant for this session")
        grant.used = True
        ref = f"SIM-{session_id[:8].upper()}"
        self.orders.append({"session_id": session_id, "order_ref": ref, "grant_id": grant.grant_id})
        return ref


class LiveBuyeeDriver:
    """Playwright against real Buyee. Only built when SHOPPER_LIVE=1 and a Buyee session
    (BUYEE_STORAGE_STATE = path to a Playwright storage_state.json the human saved after
    logging in themselves) exists. The agent never logs in and never pays."""
    name = "live-buyee-playwright"

    def __init__(self, storage_state: str, headless: bool = True):
        from playwright.sync_api import sync_playwright  # optional dependency
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._ctx = self._browser.new_context(storage_state=storage_state)
        self._page = self._ctx.new_page()

    def open_listing(self, session_id: str, listing: dict) -> str:
        url = buyee_url(listing)
        self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if "login" in self._page.url or "signup" in self._page.url:
            raise RuntimeError("Buyee asked for login; the agent does not log in. Save a session first.")
        return url

    def add_to_cart(self, session_id: str, listing: dict) -> None:
        # Buyee shopping-service items have an "Add to Cart" button; auctions have bid instead
        # (bidding is a commitment, so it is not automated).
        if listing.get("source") == "yahoo_auctions":
            raise RuntimeError("auction items need a bid, which is a commitment; stopping for the human")
        btn = self._page.get_by_role("button", name=re.compile(r"add to cart|カートに入れる", re.I))
        btn.first.click(timeout=15000)

    def read_cart(self, session_id: str) -> tuple[list[dict], str]:
        self._page.goto(BUYEE_CART, wait_until="domcontentloaded", timeout=30000)
        rows = self._page.locator("[class*=cart] [class*=item]").all_inner_texts()
        return [{"text": r[:200]} for r in rows], BUYEE_CART

    def place(self, session_id: str, grant: ApprovalGrant) -> str:
        if not isinstance(grant, ApprovalGrant) or grant.session_id != session_id or grant.used:
            raise PermissionError("place() needs an unused approval grant for this session")
        grant.used = True
        # Deliberately no payment automation: the human pays on Buyee's own page.
        return "HANDOFF:" + BUYEE_CART

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()


def default_driver():
    """Offline: FakeChromeDriver (full checkout flow, stops at awaiting_swipe).
    SHOPPER_LIVE=1: don-tacos dt-ax Chrome if installed and Accessibility-trusted, else the
    Playwright Buyee cart if BUYEE_STORAGE_STATE exists, else the fake."""
    from ..config import live
    from .chrome import FakeChromeDriver
    if not live():
        return FakeChromeDriver()
    try:
        from . import don_tacos_cua
        if don_tacos_cua.probe().get("ok"):
            return don_tacos_cua.DonTacosChromeDriver()
    except Exception:
        pass
    state = os.environ.get("BUYEE_STORAGE_STATE")
    if state and os.path.exists(state):
        try:
            return LiveBuyeeDriver(state)
        except Exception:  # playwright missing or browser not installed -> offline
            pass
    return FakeChromeDriver()
