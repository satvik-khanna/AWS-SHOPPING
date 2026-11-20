"""GuardedProvider: the PaymentProvider every kind goes through.

Guards (all kinds, offline and live alike):
  * confirm() charges only with the checkout ApprovalGrant minted by the user's swipe for
    intent.cart_session_id, and only while that session is approved/placed with that grant id;
  * intent.amount_usd must equal the approved cart's landed total (to the cent), else blocked;
  * same idempotency_key -> same intent (different params with the same key -> blocked);
  * one paid intent per cart: a double swipe returns the first result, no second charge;
  * the provider also gets the idempotency key (Stripe Idempotency-Key / PayPal-Request-Id).
"""
from __future__ import annotations

import fcntl
import json
import secrets
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path

from ..models import PaymentIntent, PaymentMethodRef

APPROVED_STATES = ("approved", "placed")


def cents(usd: float) -> int:
    return int(round(float(usd) * 100))


class Ledger:
    """Intents by idempotency key. File-backed (cross-process, flock) or in memory."""

    def __init__(self, path: Path | None = None):
        self.path = path
        self._mem: dict[str, dict] = {}

    @contextmanager
    def locked(self):
        if self.path is None:
            yield self._mem
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            raw = f.read()
            data = json.loads(raw) if raw.strip() else {}
            yield data
            f.seek(0)
            f.truncate()
            f.write(json.dumps(data, indent=1))
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)


def _default_sessions():
    from ..checkout.store import FileSessionStore
    from ..config import data_dir
    return FileSessionStore(data_dir() / "checkout")


class GuardedProvider:
    def __init__(self, kind: str, backend, sessions=None, ledger: Ledger | None = None):
        self.kind = kind
        self.backend = backend
        self.sessions = sessions if sessions is not None else _default_sessions()
        self.ledger = ledger if ledger is not None else Ledger()

    @property
    def live(self) -> bool:
        return getattr(self.backend, "live", False)

    # ---- PaymentProvider --------------------------------------------------------------------
    def methods(self, user_id: str) -> list[PaymentMethodRef]:
        return self.backend.list_methods(user_id)

    def create_intent(self, user_id: str, amount_usd: float, method_ref: str, idempotency_key: str, *,
                      cart_session_id: str) -> PaymentIntent:
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        with self.ledger.locked() as led:
            if idempotency_key in led:
                old = PaymentIntent(**led[idempotency_key])
                same = (old.kind == self.kind and old.user_id == user_id and old.method_ref == method_ref
                        and old.cart_session_id == cart_session_id and cents(old.amount_usd) == cents(amount_usd))
                if same:
                    return old
                return self._blocked_new(user_id, amount_usd, method_ref, idempotency_key, cart_session_id,
                                         "idempotency key reused with different parameters")
            paid = self._paid_for(led, cart_session_id)
            it = PaymentIntent(id="pi_local_" + secrets.token_hex(8), kind=self.kind, user_id=user_id,
                               amount_usd=round(float(amount_usd), 2), method_ref=method_ref,
                               idempotency_key=idempotency_key, cart_session_id=cart_session_id)
            reason = (f"cart {cart_session_id} already paid by {paid}" if paid
                      else self._amount_problem(it, require_approved=False))
            if reason:
                it.status, it.detail = "blocked", reason
            else:
                try:
                    it = self.backend.create(it)
                except Exception as e:  # provider error: fail closed, nothing charged
                    it.status, it.detail = "failed", f"create failed: {str(e)[:200]}"
            led[idempotency_key] = asdict(it)
            return it

    def confirm(self, intent: PaymentIntent, grant: object) -> PaymentIntent:
        with self.ledger.locked() as led:
            rec = led.get(intent.idempotency_key)
            if rec is None or rec["id"] != intent.id:
                return replace(intent, status="blocked", detail="unknown intent (not created by this provider)")
            cur = PaymentIntent(**rec)
            if cur.status in ("succeeded", "canceled", "blocked"):
                return cur                         # double swipe: same answer, no second charge
            reason = self._grant_problem(cur, grant) or self._amount_problem(cur, require_approved=True)
            if not reason:
                paid = self._paid_for(led, cur.cart_session_id)
                reason = f"cart {cur.cart_session_id} already paid by {paid}" if paid else None
            if reason:                             # refuse; the intent itself stays confirmable
                return replace(cur, status="blocked", detail=reason)
            try:
                cur = self.backend.charge(cur, grant)
            except Exception as e:
                cur = replace(cur, status="failed", detail=f"charge failed: {str(e)[:200]}")
            led[cur.idempotency_key] = asdict(cur)
            return cur

    def cancel(self, intent: PaymentIntent) -> PaymentIntent:
        with self.ledger.locked() as led:
            rec = led.get(intent.idempotency_key)
            if rec is None or rec["id"] != intent.id:
                return replace(intent, status="blocked", detail="unknown intent")
            cur = PaymentIntent(**rec)
            if cur.status in ("succeeded", "canceled"):
                return replace(cur, detail=cur.detail or f"already {cur.status}") if cur.status == "canceled" else \
                    replace(cur, detail="already succeeded; refunds are not automated")
            try:
                cur = self.backend.cancel(cur)
            except Exception as e:
                cur = replace(cur, detail=f"cancel error: {str(e)[:200]}")
            led[cur.idempotency_key] = asdict(cur)
            return cur

    # ---- guards ------------------------------------------------------------------------------
    @staticmethod
    def _paid_for(led: dict, cart_session_id: str) -> str | None:
        for r in led.values():
            if r["cart_session_id"] == cart_session_id and r["status"] == "succeeded":
                return r["id"]
        return None

    def _blocked_new(self, user_id, amount_usd, method_ref, key, sid, reason) -> PaymentIntent:
        return PaymentIntent(id="pi_blocked_" + secrets.token_hex(4), kind=self.kind, user_id=user_id,
                             amount_usd=round(float(amount_usd), 2), method_ref=method_ref,
                             idempotency_key=key, cart_session_id=sid, status="blocked", detail=reason)

    def _amount_problem(self, it: PaymentIntent, require_approved: bool) -> str | None:
        s = self.sessions.get(it.cart_session_id)
        if s is None:
            return f"no checkout session {it.cart_session_id}"
        if require_approved and s.state not in APPROVED_STATES:
            return f"cart {s.id} is {s.state}, not approved"
        if cents(s.total_usd) != cents(it.amount_usd):
            return f"amount ${it.amount_usd:.2f} != approved cart landed total ${s.total_usd:.2f}"
        return None

    def _grant_problem(self, it: PaymentIntent, grant: object) -> str | None:
        from ..checkout.machine import ApprovalGrant
        if not isinstance(grant, ApprovalGrant):
            return "no ApprovalGrant from the user's swipe"
        if grant.session_id != it.cart_session_id:
            return "ApprovalGrant is for a different cart"
        s = self.sessions.get(it.cart_session_id)
        if s is None or getattr(s, "grant_id", None) != grant.grant_id:
            return "ApprovalGrant was not recorded by the approval gate for this cart"
        return None
