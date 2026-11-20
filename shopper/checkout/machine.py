"""Checkout state machine.

    Chrome / computer-use flow (default):
    created -> listing_opened -> added_to_cart -> cart_filled -> checkout_opened
            -> shipping_filled -> awaiting_swipe -> approved -> placed
    Cart-only flow (drivers without a checkout page, e.g. the Playwright Buyee cart):
    created -> listing_opened -> added_to_cart -> cart_filled -> awaiting_approval -> approved -> placed

    awaiting_* can also end in rejected / expired / blocked (token bound to another user,
    amount changed). Any state before that can end in needs_sign_in / needs_address /
    blocked / failed, each with a one-line next step for the human.

The hard guarantee lives in the table below: the only edge into `placed` starts at
`approved`, the only edges into `approved` start at the two waiting states, and both need
an ApprovalGrant, which only the approval gate mints after a valid single-use token.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field

CREATED = "created"
LISTING_OPENED = "listing_opened"
ADDED_TO_CART = "added_to_cart"
CART_FILLED = "cart_filled"
AWAITING_APPROVAL = "awaiting_approval"
APPROVED = "approved"
PLACED = "placed"
REJECTED = "rejected"
EXPIRED = "expired"
CHECKOUT_OPENED = "checkout_opened"
SHIPPING_FILLED = "shipping_filled"
AWAITING_SWIPE = "awaiting_swipe"
NEEDS_SIGN_IN = "needs_sign_in"
NEEDS_ADDRESS = "needs_address"
BLOCKED = "blocked"
FAILED = "failed"

TRANSITIONS: dict[str, frozenset[str]] = {
    CREATED: frozenset({LISTING_OPENED, BLOCKED, FAILED}),
    LISTING_OPENED: frozenset({ADDED_TO_CART, NEEDS_SIGN_IN, BLOCKED, FAILED}),
    ADDED_TO_CART: frozenset({CART_FILLED, NEEDS_SIGN_IN, BLOCKED, FAILED}),
    CART_FILLED: frozenset({AWAITING_APPROVAL, CHECKOUT_OPENED, NEEDS_SIGN_IN, BLOCKED, FAILED}),
    CHECKOUT_OPENED: frozenset({SHIPPING_FILLED, NEEDS_SIGN_IN, NEEDS_ADDRESS, BLOCKED, FAILED}),
    SHIPPING_FILLED: frozenset({AWAITING_SWIPE, BLOCKED, FAILED}),
    AWAITING_APPROVAL: frozenset({APPROVED, REJECTED, EXPIRED, BLOCKED}),
    AWAITING_SWIPE: frozenset({APPROVED, REJECTED, EXPIRED, BLOCKED}),
    APPROVED: frozenset({PLACED, FAILED}),
    PLACED: frozenset(),
    REJECTED: frozenset(),
    NEEDS_SIGN_IN: frozenset(),
    NEEDS_ADDRESS: frozenset(),
    EXPIRED: frozenset(),
    BLOCKED: frozenset(),
    FAILED: frozenset(),
}
NEEDS_GRANT = frozenset({APPROVED, PLACED})
WAITING = frozenset({AWAITING_APPROVAL, AWAITING_SWIPE})   # the human gate
TERMINAL = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)


class IllegalTransition(RuntimeError):
    pass


_SEAL = object()


class ApprovalGrant:
    """Proof that the user's approval token was claimed for exactly one session."""
    __slots__ = ("session_id", "grant_id", "used")

    def __init__(self, session_id: str, _seal: object = None):
        if _seal is not _SEAL:
            raise PermissionError("ApprovalGrant can only be minted by the approval gate")
        self.session_id = session_id
        self.grant_id = "grant_" + secrets.token_hex(6)
        self.used = False


def _mint_grant(session_id: str) -> ApprovalGrant:
    """Private to shopper.checkout.core.BuyeeCheckout._decide. Nothing else may call this."""
    return ApprovalGrant(session_id, _seal=_SEAL)


@dataclass
class Step:
    seq: int
    at: float
    state: str          # state after this step
    actor: str          # agent | guard | human
    action: str
    detail: str = ""


@dataclass
class Session:
    id: str
    listing: dict                     # snapshot of the Listing that was picked
    total_usd: float                  # landed cost shown to the user
    driver: str
    state: str = CREATED
    steps: list[Step] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    token_hash: str | None = None     # sha256 of the approval token; the token itself is never stored
    created_at: float = 0.0
    expires_at: float | None = None
    cart: dict | None = None
    cart_url: str | None = None
    order_ref: str | None = None
    grant_id: str | None = None
    user_id: str = "default"
    binding: dict | None = None        # what the approval token is bound to: user, listing, amount, expiry
    binding_hash: str | None = None    # sha256(token | binding); recomputed at approve
    next_step: str | None = None       # one line for the human when the flow stops early
    payment: dict | None = None        # {"brand","last4","exp"} shown on the swipe UI (never the token)

    @property
    def listing_id(self) -> str:
        return self.listing.get("id", "")

    def note(self, actor: str, action: str, detail: str = "", now: float | None = None) -> None:
        self.steps.append(Step(len(self.steps) + 1, time.time() if now is None else now,
                               self.state, actor, action, detail))

    def advance(self, to: str, actor: str, action: str, detail: str = "", *,
                grant: ApprovalGrant | None = None, now: float | None = None) -> None:
        if to not in TRANSITIONS.get(self.state, frozenset()):
            raise IllegalTransition(f"{self.state} -> {to} is not allowed")
        if to in NEEDS_GRANT:
            if not isinstance(grant, ApprovalGrant) or grant.session_id != self.id:
                raise IllegalTransition(f"{self.state} -> {to} needs an approval grant for this session")
            self.grant_id = grant.grant_id
        self.state = to
        self.note(actor, action, detail, now)

    def transcript(self) -> list[str]:
        out = []
        for s in self.steps:
            clock = time.strftime("%H:%M:%S", time.localtime(s.at))
            out.append(f"{s.seq:>2}. {clock} [{s.state:<17}] {s.actor:<5} {s.action}: {s.detail}".rstrip(": "))
        return out

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        d = dict(d)
        d["steps"] = [Step(**s) for s in d.get("steps", [])]
        return cls(**d)
