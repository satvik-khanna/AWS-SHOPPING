"""BuyeeCheckout: the Checkout implementation.

fill_cart: open listing -> add to cart -> cart filled -> STOP at awaiting_approval.
approve(token): the only way to `placed`. Tokens are random, single-use, stored as sha256,
and expire. Listing text is scanned for injection and shown as warnings; it never changes
control flow.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import asdict

from ..models import CartResult, RankedPick
from . import machine as M
from .drivers import default_driver
from .guards import preflight, scan_injection
from .store import InMemorySessionStore

TTL_SECONDS = 30 * 60


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()


class BuyeeCheckout:
    def __init__(self, driver=None, store=None, budget_usd: float | None = None,
                 memory=None, user_id: str = "default", ttl: float = TTL_SECONDS, clock=time.time):
        self.driver = driver or default_driver()
        self.store = store or InMemorySessionStore()
        self.budget_usd = budget_usd       # set per request; preflight refuses picks over it
        self.memory = memory               # optional MemoryStore for approve/reject feedback
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
        s.note("agent", "start", f"{pick.listing.id} landed ${pick.cost.total_usd}", now)
        s.warnings = scan_injection(pick.listing)
        for w in s.warnings:
            s.note("guard", "listing text flagged (treated as data, ignored)", w, now)

        reason = preflight(pick, eff_budget)
        if reason:
            s.advance(M.BLOCKED, "guard", "preflight", reason, now=now)
            self.store.save(s)
            return self._result(s, detail=reason)
        try:
            url = self.driver.open_listing(s.id, s.listing)
            s.advance(M.LISTING_OPENED, "agent", "open listing", url, now=self.clock())
            self.driver.add_to_cart(s.id, s.listing)
            s.advance(M.ADDED_TO_CART, "agent", "add to cart", s.listing_id, now=self.clock())
            cart, cart_url = self.driver.read_cart(s.id)
            s.cart, s.cart_url = {"items": cart}, cart_url
            s.advance(M.CART_FILLED, "agent", "cart filled", f"{len(cart)} item(s) at {cart_url}", now=self.clock())
        except Exception as e:  # site error: fail closed
            s.advance(M.FAILED, "agent", "driver error", str(e)[:200], now=self.clock())
            self.store.save(s)
            return self._result(s, detail=str(e)[:200])

        token = secrets.token_urlsafe(16)
        s.token_hash = _hash(token)
        s.expires_at = self.clock() + self.ttl
        s.advance(M.AWAITING_APPROVAL, "agent", "STOP: waiting for human approval",
                  f"${s.total_usd} ; token expires in {int(self.ttl)}s", now=self.clock())
        self.store.save(s)
        self.store.register_token(s.token_hash, s.id)
        detail = f"cart filled on {s.driver}; nothing paid. approve(token) to place"
        if s.warnings:
            detail += f"; {len(s.warnings)} warning(s) in listing text"
        return self._result(s, token=token, detail=detail)

    # ---- human side -------------------------------------------------------------
    def approve(self, approval_token: str) -> CartResult:
        s, err = self._claim(approval_token)
        if err:
            return err
        grant = M._mint_grant(s.id)
        s.advance(M.APPROVED, "human", "approved", grant.grant_id, grant=grant, now=self.clock())
        try:
            ref = self.driver.place(s.id, grant)
        except Exception as e:
            s.advance(M.FAILED, "agent", "place error", str(e)[:200], now=self.clock())
            self.store.save(s)
            return self._result(s, detail=str(e)[:200])
        s.order_ref = ref
        detail = ("handed to human: pay on Buyee yourself at " + ref.split(":", 1)[1]
                  if ref.startswith("HANDOFF:") else f"simulated order {ref}; no real money moved")
        s.advance(M.PLACED, "agent", "placed", detail, grant=grant, now=self.clock())
        self.store.save(s)
        self._feedback("approve", s)
        return self._result(s, detail=detail)

    def reject(self, approval_token: str, reason: str = "", reject_seller: bool = False) -> CartResult:
        s, err = self._claim(approval_token)
        if err:
            return err
        s.advance(M.REJECTED, "human", "rejected", reason or "-", now=self.clock())
        self.store.save(s)
        self._feedback("reject", s, reason=reason, reject_seller=reject_seller)
        return self._result(s, detail=f"rejected: {reason or '-'}")

    def session(self, session_id: str) -> M.Session | None:
        return self.store.get(session_id)

    # ---- internals ----------------------------------------------------------------
    def _claim(self, token):
        if not isinstance(token, str) or not token or len(token) > 200:
            return None, CartResult("blocked", "", detail="unknown or already used approval token")
        sid = self.store.claim(_hash(token))
        s = self.store.get(sid) if sid else None
        if s is None:
            return None, CartResult("blocked", "", detail="unknown or already used approval token")
        if s.state != M.AWAITING_APPROVAL:
            return None, CartResult("blocked", s.listing_id, detail=f"session is {s.state}, not awaiting approval")
        if s.expires_at is not None and self.clock() > s.expires_at:
            s.advance(M.EXPIRED, "guard", "token expired", now=self.clock())
            self.store.save(s)
            return None, CartResult("blocked", s.listing_id, detail="approval token expired")
        return s, None

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
        status = s.state if s.state in ("awaiting_approval", "placed", "blocked", "failed") else "blocked"
        return CartResult(status=status, listing_id=s.listing_id, approval_token=token,
                          cart_url=s.cart_url, detail=f"[session {s.id}] {detail}", session_id=s.id)
