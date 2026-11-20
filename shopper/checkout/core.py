"""BuyeeCheckout: the Checkout implementation.

fill_cart (Chrome / computer-use drivers, the default):
    open a new Chrome tab on the listing -> add to cart -> cart -> checkout page
    -> fill shipping from the user's vault -> pick the saved payment method
    -> STOP at the payment step in state `awaiting_swipe`.
fill_cart (cart-only drivers): ... -> cart filled -> STOP in `awaiting_approval`.

approve(token[, user_id]) is the swipe and the only way to `placed`. The token is random,
single-use, stored only as sha256, and bound to user + listing + amount + expiry: a token
used by another user, after expiry, or after the amount changed (stored session edited or
the site now shows another price) does not place anything. Listing text is scanned for
injection and shown as warnings; it never changes control flow. The agent never types a
password, card number or CVV: sign-in stays in the user's own Chrome profile.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict

from ..models import CartResult, RankedPick, ShippingAddress
from . import machine as M
from .drivers import default_driver
from .guards import UnsafeUrl, preflight, scan_injection
from .store import InMemorySessionStore

TTL_SECONDS = 30 * 60
# Offline only: used when the user has no saved address so the demo can reach the swipe step.
DEMO_ADDRESS = ShippingAddress(name="Demo Shopper", line1="1 Example Street", city="San Francisco",
                               postal_code="94000", country="US", region="CA")
_BLOCKED = CartResult("blocked", "", detail="unknown or already used approval token")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()


def _bind(token: str, binding: dict) -> str:
    msg = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(token.encode("utf-8", "replace"), msg, hashlib.sha256).hexdigest()


def address_fields(a: ShippingAddress) -> dict:
    d = {"full_name": a.name, "address_line1": a.line1, "address_line2": a.line2, "city": a.city,
         "region": a.region, "postal_code": a.postal_code, "country": a.country, "phone": a.phone}
    return {k: v for k, v in d.items() if v}


class BuyeeCheckout:
    def __init__(self, driver=None, store=None, budget_usd: float | None = None,
                 memory=None, user_id: str = "default", ttl: float = TTL_SECONDS, clock=time.time,
                 vault=None):
        from ..vault import MemoryVault
        self.driver = driver or default_driver()
        self.store = store or InMemorySessionStore()
        self.budget_usd = budget_usd       # set per request; preflight refuses picks over it
        self.memory = memory               # optional MemoryStore for approve/reject feedback
        self.vault = vault if vault is not None else MemoryVault()
        self.user_id = user_id
        self.ttl = ttl
        self.clock = clock

    # ---- agent side -------------------------------------------------------------
    def fill_cart(self, pick: RankedPick, *, budget_usd: float | None = None,
                  user_id: str | None = None) -> CartResult:
        now = self.clock()
        eff_budget = self.budget_usd if budget_usd is None else budget_usd
        eff_user = user_id or self.user_id
        s = M.Session(id=secrets.token_hex(8), listing=asdict(pick.listing), total_usd=pick.cost.total_usd,
                      driver=getattr(self.driver, "name", "?"), created_at=now, user_id=eff_user)
        s.note("agent", "start", f"{pick.listing.id} landed ${pick.cost.total_usd} for user {eff_user}", now)
        s.warnings = scan_injection(pick.listing)
        for w in s.warnings:
            s.note("guard", "listing text flagged (treated as data, ignored)", w, now)

        reason = preflight(pick, eff_budget)
        if reason:
            s.advance(M.BLOCKED, "guard", "preflight", reason, now=now)
            self.store.save(s)
            return self._result(s, detail=reason)
        price_jpy = None
        try:
            tab = "open new Chrome tab on listing" if getattr(self.driver, "has_checkout", False) else "open listing"
            url = self.driver.open_listing(s.id, s.listing)
            s.advance(M.LISTING_OPENED, "agent", tab, url, now=self.clock())
            self.driver.add_to_cart(s.id, s.listing)
            s.advance(M.ADDED_TO_CART, "agent", "add to cart", s.listing_id, now=self.clock())
            cart, cart_url = self.driver.read_cart(s.id)
            s.cart, s.cart_url = {"items": cart}, cart_url
            s.advance(M.CART_FILLED, "agent", "cart filled", f"{len(cart)} item(s) at {cart_url}", now=self.clock())
            if getattr(self.driver, "has_checkout", False):
                stop = self._checkout_page(s)
                if stop:
                    return stop
                price_jpy = self._read_price(s)
        except UnsafeUrl as e:
            s.advance(M.BLOCKED, "guard", "unsafe URL refused", str(e)[:200], now=self.clock())
            s.next_step = "The listing link is not a normal web address; nothing was opened or paid."
            self.store.save(s)
            return self._result(s, detail=str(e)[:200])
        except Exception as e:  # site error: fail closed
            s.advance(M.FAILED, "agent", "driver error", str(e)[:200], now=self.clock())
            s.next_step = "The page did not behave as expected; nothing was paid. Press Buy again or open the listing yourself."
            self.store.save(s)
            return self._result(s, detail=str(e)[:200])

        token = secrets.token_urlsafe(16)
        s.token_hash = _hash(token)
        s.expires_at = self.clock() + self.ttl
        s.binding = {"user_id": s.user_id, "listing_id": s.listing_id, "amount_usd": round(s.total_usd, 2),
                     "item_price_jpy": price_jpy, "expires_at": s.expires_at, "session_id": s.id}
        s.binding_hash = _bind(token, s.binding)
        if getattr(self.driver, "has_checkout", False):
            pay = f" with {s.payment['brand']} ending {s.payment['last4']}" if s.payment else ""
            s.advance(M.AWAITING_SWIPE, "agent", "STOP at payment step: waiting for the user's swipe",
                      f"${s.total_usd}{pay}; token bound to {s.user_id}, expires in {int(self.ttl)}s", now=self.clock())
            detail = f"checkout filled on {s.driver} up to the payment step; nothing paid. Swipe (approve) to place"
        else:
            s.advance(M.AWAITING_APPROVAL, "agent", "STOP: waiting for human approval",
                      f"${s.total_usd} ; token expires in {int(self.ttl)}s", now=self.clock())
            detail = f"cart filled on {s.driver}; nothing paid. approve(token) to place"
        self.store.save(s)
        self.store.register_token(s.token_hash, s.id)
        if s.warnings:
            detail += f"; {len(s.warnings)} warning(s) in listing text"
        return self._result(s, token=token, detail=detail)

    def _checkout_page(self, s: M.Session) -> CartResult | None:
        from ..config import live
        from ..vault import default_payment
        page = self.driver.open_checkout(s.id, s.listing)
        site = page.get("site") or ""
        if page.get("page") == "sign_in":
            self._vault_signed_in(s, site, False)
            s.next_step = (f"Sign in to {site} once in your own Chrome (the agent never types passwords), "
                           f"then press Buy again; Chrome will remember it.")
            s.advance(M.NEEDS_SIGN_IN, "agent", "STOP: site needs sign-in", s.next_step, now=self.clock())
            self.store.save(s)
            return self._result(s, detail=s.next_step)
        self._vault_signed_in(s, site, True)
        s.advance(M.CHECKOUT_OPENED, "agent", "go to checkout page", page.get("url", ""), now=self.clock())

        rec = self.vault.get(s.user_id)
        addr, source = rec.address, "vault"
        if addr is None:
            if live():
                s.next_step = ("Save a shipping address once: python3 -m shopper vault address --user "
                               f"{s.user_id} --name ... --line1 ... --city ... --postal ... --country US")
                s.advance(M.NEEDS_ADDRESS, "agent", "STOP: no shipping address saved", s.next_step, now=self.clock())
                self.store.save(s)
                return self._result(s, detail=s.next_step)
            addr, source = DEMO_ADDRESS, "offline demo placeholder (no saved address)"
        filled = self.driver.fill_shipping(s.id, address_fields(addr))
        s.advance(M.SHIPPING_FILLED, "agent", "fill shipping",
                  f"{', '.join(filled)} from {source}", now=self.clock())   # field names only, not the values
        pm = default_payment(rec)
        s.payment = {"brand": pm.brand, "last4": pm.last4, "exp": pm.exp} if pm else None
        chosen = self.driver.select_payment(s.id, dict(s.payment) if s.payment else None)
        s.note("agent", "select saved payment method", f"{chosen} (no card number or CVV typed)", self.clock())
        return None

    def _vault_signed_in(self, s: M.Session, site: str, ok: bool) -> None:
        if not site:
            return
        try:
            self.vault.set_signed_in(s.user_id, site, ok)
            s.note("agent", "vault: remember sign-in" if ok else "vault: not signed in", site, self.clock())
        except Exception as e:  # vault must never break checkout
            s.note("agent", "vault update failed", str(e)[:120], self.clock())

    def _read_price(self, s: M.Session) -> int | None:
        fn = getattr(self.driver, "read_item_price_jpy", None)
        if not callable(fn):
            return None
        try:
            return fn(s.id)
        except Exception:
            return None

    # ---- human side -------------------------------------------------------------
    def approve(self, approval_token: str, user_id: str | None = None) -> CartResult:
        """The user's swipe. The only path that may place an order."""
        s, err = self._claim(approval_token, user_id)
        if err:
            return err
        why = self._binding_problem(s, approval_token)
        if why:
            s.advance(M.BLOCKED, "guard", "approval refused", why, now=self.clock())
            s.next_step = "Nothing was paid. Press Buy again to see the current total and swipe again."
            self.store.save(s)
            return self._result(s, detail=f"{why}; nothing paid")
        grant = M._mint_grant(s.id)
        s.advance(M.APPROVED, "human", "swiped / approved", grant.grant_id, grant=grant, now=self.clock())
        try:
            ref = self.driver.place(s.id, grant)
        except Exception as e:
            s.advance(M.FAILED, "agent", "place error", str(e)[:200], now=self.clock())
            self.store.save(s)
            return self._result(s, detail=str(e)[:200])
        s.order_ref = ref
        if ref.startswith("HANDOFF:"):
            detail = "handed to you: finish payment on the site yourself at " + ref.split(":", 1)[1]
        else:
            detail = f"simulated order {ref}; no real money moved"
        s.advance(M.PLACED, "agent", "placed", detail, grant=grant, now=self.clock())
        self.store.save(s)
        self._feedback("approve", s)
        return self._result(s, detail=detail)

    def reject(self, approval_token: str, reason: str = "", reject_seller: bool = False,
               user_id: str | None = None) -> CartResult:
        s, err = self._claim(approval_token, user_id)
        if err:
            return err
        s.advance(M.REJECTED, "human", "rejected", reason or "-", now=self.clock())
        self.store.save(s)
        self._feedback("reject", s, reason=reason, reject_seller=reject_seller)
        return self._result(s, detail=f"rejected: {reason or '-'}")

    def session(self, session_id: str) -> M.Session | None:
        return self.store.get(session_id)

    # ---- internals ----------------------------------------------------------------
    def _claim(self, token, user_id: str | None = None):
        if not isinstance(token, str) or not token or len(token) > 200:
            return None, _BLOCKED
        h = _hash(token)
        if user_id is not None:
            # check the owner before using the token up, so another user cannot burn it
            peek = getattr(self.store, "peek", None)
            sid0 = peek(h) if callable(peek) else None
            s0 = self.store.get(sid0) if sid0 else None
            if s0 is not None and s0.user_id != user_id:
                s0.note("guard", "approval by another user refused", f"token belongs to {s0.user_id}", self.clock())
                self.store.save(s0)
                return None, CartResult("blocked", s0.listing_id, detail="this approval token belongs to another user",
                                        session_id=s0.id)
        sid = self.store.claim(h)
        s = self.store.get(sid) if sid else None
        if s is None:
            return None, _BLOCKED
        if user_id is not None and s.user_id != user_id:   # lost a race with an owner change: fail closed
            return None, CartResult("blocked", s.listing_id, detail="this approval token belongs to another user")
        if s.state not in M.WAITING:
            return None, CartResult("blocked", s.listing_id, detail=f"session is {s.state}, not awaiting approval")
        if s.expires_at is not None and self.clock() > s.expires_at:
            s.advance(M.EXPIRED, "guard", "token expired", now=self.clock())
            s.next_step = "The approval expired; nothing was paid. Press Buy again."
            self.store.save(s)
            return None, CartResult("blocked", s.listing_id, detail="approval token expired", session_id=s.id)
        return s, None

    def _binding_problem(self, s: M.Session, token: str) -> str | None:
        b = s.binding
        if b is None:
            return None                     # sessions created before binding existed
        if not s.binding_hash or not hmac.compare_digest(_bind(token, b), s.binding_hash):
            return "approval binding does not match (session was modified)"
        if (b.get("user_id") != s.user_id or b.get("listing_id") != s.listing_id
                or b.get("session_id") != s.id or abs(float(b.get("amount_usd", -1)) - round(s.total_usd, 2)) > 0.005):
            return f"amount or item changed after approval was issued (${b.get('amount_usd')} -> ${s.total_usd})"
        if b.get("expires_at") != s.expires_at:
            return "expiry changed after approval was issued"
        want = b.get("item_price_jpy")
        if want is not None:
            now_price = self._read_price(s)
            if now_price is not None and int(now_price) != int(want):
                return f"price on the site changed from ¥{want} to ¥{now_price} after approval was issued"
            s.note("guard", "price re-checked on payment step",
                   f"¥{now_price}" if now_price is not None else "page not readable; using bound amount", self.clock())
        return None

    def _feedback(self, kind: str, s: M.Session, **kw) -> None:
        if self.memory is None:
            return
        try:
            from ..memory.feedback import apply_feedback
            apply_feedback(self.memory, s.user_id or self.user_id, kind, s.listing, **kw)
            s.note("agent", f"memory updated ({kind})", now=self.clock())
            self.store.save(s)
        except Exception as e:  # memory must never break checkout
            s.note("agent", "memory update failed", str(e)[:120], now=self.clock())

    def _result(self, s: M.Session, token: str | None = None, detail: str = "") -> CartResult:
        # CartResult.status is shared with other modules: both waiting states read as awaiting_approval,
        # needs_* read as blocked. The exact state is in the session (GET /api/sessions/<id>).
        status = {M.AWAITING_SWIPE: "awaiting_approval"}.get(s.state, s.state)
        if status not in ("awaiting_approval", "placed", "blocked", "failed"):
            status = "blocked"
        return CartResult(status=status, listing_id=s.listing_id, approval_token=token,
                          cart_url=s.cart_url, detail=f"[session {s.id}] [{s.state}] {detail}", session_id=s.id)
