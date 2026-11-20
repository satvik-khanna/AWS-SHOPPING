"""Offline stand-in for "a new Chrome tab worked by computer use".

FakeChromeDriver simulates the pages the live DonTacosChromeDriver walks:
listing page -> cart -> checkout page (or a sign-in wall) -> shipping form -> payment step.
It records every field it typed so tests can prove no password / card number / CVV was ever
typed, and `place()` refuses without an ApprovalGrant. No network, no browser.
"""
from __future__ import annotations

from .drivers import buyee_url
from .guards import check_open_url
from .machine import ApprovalGrant

SECRET_HINTS = ("password", "passwd", "cvv", "cvc", "card number", "cardnumber", "security code")


def site_of(url: str) -> str:
    from ..vault import normalize_site
    return normalize_site(url)


class FakeChromeDriver:
    name = "fake-chrome"
    has_checkout = True

    def __init__(self, signed_in_sites: set[str] | None = None, fail_at: str | None = None):
        # sites the fake Chrome profile is signed into (the real one is the user's own profile)
        self.signed_in_sites = {"buyee.jp"} if signed_in_sites is None else set(signed_in_sites)
        self.fail_at = fail_at
        self.tabs: dict[str, dict] = {}
        self.typed: list[tuple[str, str, str]] = []     # (session, field, value)
        self.orders: list[dict] = []
        self.pressed: list[tuple[str, str]] = []        # (session, button)

    def _fail(self, step: str) -> None:
        if self.fail_at == step:
            raise RuntimeError(f"simulated page error at {step}")

    def _tab(self, sid: str) -> dict:
        t = self.tabs.get(sid)
        if t is None:
            raise RuntimeError("no Chrome tab for this session")
        return t

    # -- pages --------------------------------------------------------------------
    def open_listing(self, sid: str, listing: dict) -> str:
        self._fail("open")
        fields = listing.get("fields") if isinstance(listing.get("fields"), dict) else {}
        url = check_open_url(str(fields.get("buyee_url") or "") or buyee_url(listing))
        self.tabs[sid] = {"url": url, "site": site_of(url), "page": "listing", "cart": [],
                          "price_jpy": int(listing.get("price_jpy") or 0), "shipping": {}, "payment": None}
        return url

    def add_to_cart(self, sid: str, listing: dict) -> None:
        self._fail("add")
        t = self._tab(sid)
        t["cart"].append({"listing_id": listing.get("id"), "price_jpy": t["price_jpy"], "qty": 1})
        self.pressed.append((sid, "Add to cart"))

    def read_cart(self, sid: str) -> tuple[list[dict], str]:
        self._fail("read")
        t = self._tab(sid)
        t["page"] = "cart"
        return list(t["cart"]), f"https://{t['site']}/cart#fake-{sid[:8]}"

    def open_checkout(self, sid: str, listing: dict) -> dict:
        self._fail("checkout")
        t = self._tab(sid)
        if t["site"] not in self.signed_in_sites:
            t["page"] = "sign_in"
            return {"page": "sign_in", "site": t["site"], "url": f"https://{t['site']}/signin"}
        t["page"] = "checkout"
        return {"page": "checkout", "site": t["site"], "url": f"https://{t['site']}/checkout#fake-{sid[:8]}"}

    def fill_shipping(self, sid: str, fields: dict) -> list[str]:
        self._fail("shipping")
        from ..vault import check_secret_free
        check_secret_free(fields)
        t = self._tab(sid)
        if t["page"] != "checkout":
            raise RuntimeError(f"not on the checkout page ({t['page']})")
        for k, v in fields.items():
            if any(h in k.lower() for h in SECRET_HINTS):
                raise PermissionError(f"refusing to type into {k!r}")
            self.typed.append((sid, k, str(v)))
            t["shipping"][k] = str(v)
        return sorted(fields)

    def select_payment(self, sid: str, pm: dict | None) -> str:
        t = self._tab(sid)
        t["payment"] = pm
        t["page"] = "payment"
        if pm:
            self.pressed.append((sid, f"saved card {pm.get('brand')} ending {pm.get('last4')}"))
            return f"{pm.get('brand')} ending {pm.get('last4')}"
        return "site default payment method"

    def read_item_price_jpy(self, sid: str) -> int | None:
        return self._tab(sid)["price_jpy"]

    # -- firedrill knob -------------------------------------------------------------
    def reprice(self, sid: str, new_price_jpy: int) -> None:
        """The seller changes the price while the user looks at the swipe UI."""
        self._tab(sid)["price_jpy"] = int(new_price_jpy)

    # -- the one irreversible step ------------------------------------------------------
    def place(self, sid: str, grant: ApprovalGrant) -> str:
        if not isinstance(grant, ApprovalGrant) or grant.session_id != sid or grant.used:
            raise PermissionError("place() needs an unused approval grant for this session")
        t = self.tabs.get(sid)
        if t is None:      # approve came from a later process: the fake re-opens the saved payment step
            t = self.tabs[sid] = {"page": "payment", "price_jpy": None}
        if t["page"] != "payment":
            raise RuntimeError(f"not on the payment step ({t['page']})")
        grant.used = True
        self.pressed.append((sid, "Place order"))
        ref = f"FAKE-{sid[:8].upper()}"
        self.orders.append({"session_id": sid, "order_ref": ref, "grant_id": grant.grant_id,
                            "price_jpy": t["price_jpy"]})
        t["page"] = "done"
        return ref
