"""Chat intents and the typed UI components the agent answers with. Pure functions, no HTTP.

Intent parsing is rule-based (offline). Components are plain JSON dicts:
every one has {"type": <name>, "version": COMPONENT_VERSION}. Schema: docs/API.md#components.
"""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..models import CartResult, RankedPick, ShoppingRequest

COMPONENT_VERSION = 1
DEFAULT_BUDGET_USD = 1000.0
MAX_ROWS = 10
SORT_KEYS = ("relevance", "price", "condition")
COND_RANK = {"new": 4, "like_new": 3, "good": 2, "fair": 1, "junk": 0}

# ---------------------------------------------------------------- session state


@dataclass
class ChatSession:
    id: str
    user: str
    request: ShoppingRequest | None = None
    picks: list[RankedPick] = field(default_factory=list)   # relevance order
    sort: tuple[str, str] = ("relevance", "asc")
    cart_token: str | None = None          # pending approval token from the last Buy
    payment_method: dict | None = None     # masked only: brand / last4 / exp
    updated_at: float = field(default_factory=time.time)


def new_session_id() -> str:
    return "chat_" + secrets.token_urlsafe(9)


# ---------------------------------------------------------------- intents

_BUY = re.compile(r"^\s*(?:buy|purchase|order)\s+(?:#|no\.?\s*|number\s+)?(?P<target>\S+)\s*$", re.I)
_SORT = re.compile(r"\b(?:sort|order|rank)\s*(?:it|them|the list|results)?\s*by\s+(?P<key>[a-z_ ]+?)"
                   r"(?:\s+(?P<dir>asc|ascending|desc|descending|low to high|high to low))?\s*$", re.I)
_SHORT_SORT = {"cheapest first": ("price", "asc"), "cheapest": ("price", "asc"),
               "most expensive": ("price", "desc"), "best condition": ("condition", "desc"),
               "best match": ("relevance", "asc")}
_APPROVE = re.compile(r"^\s*(approve|confirm|pay|yes,? (?:buy|pay))\b", re.I)
_REJECT = re.compile(r"^\s*(reject|skip|cancel|no thanks)\b", re.I)
_HELP = re.compile(r"^\s*(help|\?|what can you do)\s*\??$", re.I)
_LIST_ONLY = re.compile(r"^\s*(list(?: it| them)? up|show (?:the )?list|list|show (?:me )?again)\s*$", re.I)
_LEAD = re.compile(r"^\s*(?:please\s+)?(?:list up|list|search(?: for)?|find(?: me)?|look for|show me|"
                   r"i want(?: to buy)?|i'm looking for|get)\s+", re.I)
_BUDGET = re.compile(r"[,;]?\s*\b(?:under|below|up to|max(?:imum)?|less than|budget(?: of)?|within)?\s*"
                     r"(?:\$|usd\s*)\s?(?P<n>[\d,]+(?:\.\d+)?)(?:\s*(?:usd|dollars))?"
                     r"(?:\s+(?:landed|delivered|total|all[- ]in))?", re.I)
_BUDGET_WORD = re.compile(r"[,;]?\s*\b(?:under|below|up to|less than|budget(?: of)?)\s+(?P<n>[\d,]+(?:\.\d+)?)"
                          r"(?:\s*(?:usd|dollars))?\b", re.I)
_SHIP = re.compile(r"[,;]?\s*\b(?:shipped|delivered|shipping|ship)\s+to\s+(?P<to>[A-Za-z][A-Za-z -]{1,30})$", re.I)
_COND = {"new": "new", "like new": "like_new", "mint": "like_new", "good condition": "good"}


def parse_sort(key: str, direction: str | None) -> tuple[str, str] | None:
    key = key.strip().lower()
    if key in ("price", "landed", "landed price", "total", "cost", "cheapest"):
        k = "price"
    elif key in ("condition", "quality", "cond"):
        k = "condition"
    elif key in ("relevance", "best", "best match", "match", "score", "default"):
        k = "relevance"
    else:
        return None
    d = (direction or "").lower()
    if d in ("desc", "descending", "high to low"):
        return k, "desc"
    if d in ("asc", "ascending", "low to high"):
        return k, "asc"
    return k, ("desc" if k == "condition" else "asc")


def parse_intent(message: str) -> tuple[str, dict]:
    m = message.strip()
    if not m or _HELP.match(m):
        return "help", {}
    if b := _BUY.match(m):
        return "buy", {"target": b.group("target").strip(".,!")}
    low = m.lower().strip(" .!")
    if low in _SHORT_SORT or low.replace("sort ", "") in _SHORT_SORT:
        k, d = _SHORT_SORT.get(low) or _SHORT_SORT[low.replace("sort ", "")]
        return "sort", {"key": k, "dir": d}
    if s := _SORT.search(m):
        parsed = parse_sort(s.group("key"), s.group("dir"))
        if parsed:
            return "sort", {"key": parsed[0], "dir": parsed[1]}
        return "bad_sort", {"key": s.group("key").strip()}
    if _APPROVE.match(m):
        return "approve", {}
    if _REJECT.match(m):
        return "reject", {}
    if _LIST_ONLY.match(m):
        return "list", {}
    return "search", parse_search(m)


def parse_search(message: str) -> dict:
    """'list up Fujifilm X100V under $900 shipped to US-CA' -> query/budget/ship_to/condition_min."""
    text = message.strip()
    out: dict = {}
    if s := _SHIP.search(text):
        out["ship_to"] = s.group("to").strip()
        text = text[:s.start()]
    b = _BUDGET.search(text) or _BUDGET_WORD.search(text)
    if b:
        try:
            out["budget"] = float(b.group("n").replace(",", ""))
            text = text[:b.start()] + text[b.end():]
        except ValueError:
            pass
    low = text.lower()
    for word, cond in _COND.items():
        if re.search(rf"\b{word}\b", low):
            out["condition_min"] = cond
    text = _LEAD.sub("", text)
    text = re.sub(r"^\s*(?:a|an|the|some|used)\s+", "", text, flags=re.I)
    out["query"] = re.sub(r"\s+", " ", text).strip(" ,.;") or message.strip()
    return out


# ---------------------------------------------------------------- sorting


def sorted_picks(picks: list[RankedPick], key: str, direction: str) -> list[RankedPick]:
    if key == "price":
        out = sorted(picks, key=lambda p: p.cost.total_usd)
    elif key == "condition":
        out = sorted(picks, key=lambda p: COND_RANK.get(p.listing.condition, -1))
    else:
        return list(picks) if direction == "asc" else list(reversed(picks))
    return out if direction == "asc" else list(reversed(out))


# ---------------------------------------------------------------- payment method (masked only)


def parse_payment_method(raw) -> dict | None:
    """Accept only brand + last4 + exp (+ opaque provider token). Never a card number or CVV."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("payment_method must be an object {brand, last4, exp}")
    allowed = {"brand", "last4", "exp", "provider_token"}
    extra = set(raw) - allowed
    if extra:
        raise ValueError(f"payment_method accepts only brand, last4, exp, provider_token (got {', '.join(sorted(extra))})")
    brand = raw.get("brand")
    last4 = str(raw.get("last4") or "")
    exp = str(raw.get("exp") or "")
    if not isinstance(brand, str) or not re.fullmatch(r"[A-Za-z][A-Za-z .-]{0,19}", brand):
        raise ValueError("payment_method.brand must be a card brand name like \"Visa\"")
    if not re.fullmatch(r"\d{4}", last4):
        raise ValueError("payment_method.last4 must be exactly the last 4 digits (never the full number)")
    if not re.fullmatch(r"(0[1-9]|1[0-2])/\d{2}", exp):
        raise ValueError("payment_method.exp must be MM/YY")
    tok = raw.get("provider_token")
    if tok is not None and (not isinstance(tok, str) or not re.fullmatch(r"[A-Za-z0-9_\-]{4,80}", tok)
                            or re.search(r"\d{12,}", tok)):
        raise ValueError("payment_method.provider_token must be an opaque provider token (not a card number)")
    return {"brand": brand, "last4": last4, "exp": exp, "label": f"{brand} •••• {last4}",
            "has_provider_token": bool(tok)}


# ---------------------------------------------------------------- components


def _c(type_: str, **body) -> dict:
    return {"type": type_, "version": COMPONENT_VERSION, **body}


def notice(text: str, level: str = "info") -> dict:
    return _c("notice", level=level, text=text)


def buy_body(req: ShoppingRequest, session_id: str, listing_id: str) -> dict:
    return {"query": req.query, "budget": req.max_total_usd, "ship_to": req.ship_to, "user": req.user_id,
            "condition_min": req.condition_min, "listing_id": listing_id, "session": session_id}


def listing_table(s: ChatSession) -> dict:
    key, direction = s.sort
    rel = {p.listing.id: i + 1 for i, p in enumerate(s.picks)}
    rows = []
    for p in sorted_picks(s.picks, key, direction):
        l = p.listing
        rows.append({
            "id": l.id, "relevance_rank": rel[l.id], "title": l.title_en or l.title_ja, "title_ja": l.title_ja,
            "total_usd": p.cost.total_usd, "price_jpy": l.price_jpy, "condition": l.condition,
            "source": l.source, "seller": l.seller, "url": l.url,
            "image": l.photos[0] if l.photos else None, "score": round(p.score, 4), "reason": p.reason,
            "actions": [{"type": "buy", "label": "Buy", "method": "POST", "path": "/api/buy",
                         "body": buy_body(s.request, s.id, l.id)},
                        {"type": "reject_seller", "label": f"Never show {l.seller}", "method": "POST",
                         "path": "/api/reject", "body": {"user": s.user, "seller": l.seller}}],
        })
    return _c("listing_table", session=s.id, query=s.request.query if s.request else "",
              budget_usd=s.request.max_total_usd if s.request else None,
              columns=[{"key": "title", "label": "Item", "sortable": False},
                       {"key": "price", "field": "total_usd", "label": "Landed (USD)", "sortable": True},
                       {"key": "condition", "field": "condition", "label": "Condition", "sortable": True},
                       {"key": "relevance", "field": "relevance_rank", "label": "Best match", "sortable": True},
                       {"key": "source", "label": "Source", "sortable": False},
                       {"key": "seller", "label": "Seller", "sortable": False}],
              sort={"key": key, "dir": direction, "options": list(SORT_KEYS),
                    "chat": "sort by price | sort by condition | sort by relevance"},
              rows=rows)


def cost_breakdown(p: RankedPick) -> dict:
    return _c("cost_breakdown", listing_id=p.listing.id, title=p.listing.title_en or p.listing.title_ja,
              lines=[{"label": ln.label, "amount_usd": ln.amount_usd,
                      "citation": {"source": ln.citation.source, "as_of": ln.citation.as_of, "url": ln.citation.url}}
                     for ln in p.cost.lines],
              total_usd=p.cost.total_usd, fx_jpy_per_usd=p.cost.fx_jpy_per_usd)


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def agent_browser(p: RankedPick, cart: CartResult, session, cua: dict | None) -> dict:
    """session = checkout session (shopper.checkout.machine.Session) or None."""
    status = {"awaiting_approval": "cart_ready", "placed": "placed"}.get(cart.status, cart.status)
    fields = p.listing.fields or {}
    cua = cua or {}
    opened = cua.get("opened_url") or fields.get("buyee_url") or p.listing.url
    steps = [{"seq": st.seq, "at": st.at, "state": st.state, "actor": st.actor, "action": st.action,
              "detail": st.detail} for st in (session.steps if session else [])]
    return _c("agent_browser", status=status, listing_id=p.listing.id, opened_url=opened,
              listing_url=p.listing.url, cart_url=cart.cart_url, opened_in_browser=bool(cua.get("opened_in_chrome")),
              driver=session.driver if session else None, checkout_session=cart.session_id,
              steps=steps, warnings=list(session.warnings) if session else [], detail=cart.detail)


def swipe_to_pay(p: RankedPick, cart: CartResult, session, user: str, payment_method: dict | None) -> dict:
    exp = session.expires_at if session else None
    return _c("swipe_to_pay", approval_token=cart.approval_token, listing_id=p.listing.id,
              title=p.listing.title_en or p.listing.title_ja, amount_usd=p.cost.total_usd, currency="USD",
              payment_method=payment_method, expires_at=exp, expires_at_iso=_iso(exp),
              approve={"method": "POST", "path": "/api/approve", "body": {"approval_token": cart.approval_token}},
              reject={"method": "POST", "path": "/api/reject",
                      "body": {"user": user, "seller": p.listing.seller, "approval_token": cart.approval_token,
                               "reason": "declined at swipe"}},
              label=f"Slide to pay ${p.cost.total_usd:,.2f}")
