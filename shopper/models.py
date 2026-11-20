"""Shared data types. Every module talks through these; change only with a reason in the commit."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass
class ShoppingRequest:
    """What the user asked for, parsed. e.g. 'used Fujifilm X100V under $900, shipped to California'."""
    query: str                      # free text, English
    max_total_usd: float            # budget for the full landed cost
    ship_to: str                    # e.g. "US-CA"
    user_id: str = "default"
    condition_min: str | None = None  # "new" | "like_new" | "good" | "fair" | None


@dataclass
class Preferences:
    """Recalled from memory before searching."""
    user_id: str
    sizes: dict[str, str] = field(default_factory=dict)
    condition_min: str | None = None
    rejected_sellers: list[str] = field(default_factory=list)
    past_purchases: list[str] = field(default_factory=list)   # listing ids or titles
    notes: list[str] = field(default_factory=list)


@dataclass
class Listing:
    """One marketplace listing after translation/extraction."""
    id: str                         # "<source>:<native id>"
    source: Literal["mercari", "yahoo_auctions", "yahoo_fleamarket", "other"]
    url: str
    title_ja: str
    title_en: str
    price_jpy: int
    condition: str                  # normalized: new | like_new | good | fair | junk
    seller: str
    ships_abroad: bool              # seller ships internationally (proxy still usable if False)
    weight_kg: float | None = None
    category: str | None = None     # used for duty rules, e.g. "camera"
    fields: dict[str, str] = field(default_factory=dict)  # extracted specs
    photos: list[str] = field(default_factory=list)


@dataclass
class Citation:
    """Where a number came from. Every cost line must have one."""
    source: str                     # e.g. "senso:buyee-fees" or "rules/fees.json#buyee_service_fee"
    as_of: str                      # YYYY-MM-DD of the rule
    url: str | None = None


@dataclass
class CostLine:
    label: str                      # "item price", "proxy service fee", "domestic shipping", "intl shipping", "duty", ...
    amount_usd: float
    citation: Citation


@dataclass
class LandedCost:
    listing_id: str
    lines: list[CostLine]
    fx_jpy_per_usd: float
    blocked_reason: str | None = None   # banned item / cannot ship -> not buyable

    @property
    def total_usd(self) -> float:
        return round(sum(l.amount_usd for l in self.lines), 2)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["total_usd"] = self.total_usd
        return out


@dataclass
class RankedPick:
    listing: Listing
    cost: LandedCost
    score: float                    # higher is better
    reason: str                     # human-readable explanation of why


@dataclass
class CartResult:
    status: Literal["awaiting_approval", "placed", "blocked", "failed"]
    listing_id: str
    approval_token: str | None = None   # required to place; None once placed/blocked
    cart_url: str | None = None
    detail: str = ""
    session_id: str | None = None


@dataclass
class PipelineResult:
    request: ShoppingRequest
    preferences: Preferences
    picks: list[RankedPick]
    cart: CartResult | None
    trail: list[str]                # step-by-step reasoning trail for the demo


@dataclass
class ShippingAddress:
    """Per-user shipping address kept in the vault (gitignored data dir, 0600)."""
    name: str
    line1: str
    city: str
    postal_code: str
    country: str                    # ISO 3166-1 alpha-2, e.g. "US"
    line2: str = ""
    region: str = ""                # state / prefecture, e.g. "CA"
    phone: str = ""


@dataclass
class PaymentMethodRef:
    """A reference to a card the user saved with a provider / the site. Never a card number or CVV."""
    token: str                      # provider token, e.g. "pm_..." / "tok_..."
    brand: str                      # visa | mastercard | amex | jcb | ...
    last4: str                      # exactly 4 digits
    exp: str                        # "MM/YY"
    label: str = ""
    kind: str = "card"              # card | stripe | paypal | venmo (payments); paypal/venmo use brand=kind, last4="", exp=""


@dataclass
class VaultRecord:
    user_id: str
    address: ShippingAddress | None = None
    signed_in_sites: list[str] = field(default_factory=list)   # sites this user's Chrome profile is signed into
    payment_methods: list[PaymentMethodRef] = field(default_factory=list)
    default_payment: str | None = None                          # token of the default PaymentMethodRef


# ---- payments (owner: shopper/payments) ------------------------------------------------------
PaymentKind = Literal["card", "stripe", "paypal", "venmo"]
PaymentStatus = Literal["requires_approval", "payer_action_required", "succeeded", "canceled", "blocked", "failed"]


@dataclass
class PaymentIntent:
    """One attempt to pay the approved cart. amount_usd must equal the approved cart's landed total."""
    id: str                         # local id "pi_local_..."
    kind: PaymentKind
    user_id: str
    amount_usd: float
    method_ref: str                 # PaymentMethodRef.token
    idempotency_key: str            # same key -> same intent; sent to the provider as its idempotency header
    cart_session_id: str            # checkout session whose ApprovalGrant can confirm this intent
    status: PaymentStatus = "requires_approval"
    provider_ref: str | None = None # Stripe pi_..., PayPal order id; None offline
    currency: str = "usd"
    approve_url: str | None = None  # PayPal / Venmo buyer approval link when the provider needs one
    detail: str = ""
