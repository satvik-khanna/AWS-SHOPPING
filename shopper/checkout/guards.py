"""Pre-cart guards. Listing text is data: it is scanned and flagged for the human,
and it never changes the control flow of the checkout."""
from __future__ import annotations

import math
import re

from ..models import Listing, RankedPick

_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions?|rules?|prompts?)", "ignore-instructions"),
    (r"\b(buy|purchase|order|pay|check\s?out)\s+(it\s+|this\s+)?(right\s+)?(now|immediately|at once)\b", "buy-now"),
    (r"\b(skip|bypass|without|no need for|disable)\s+(the\s+)?(user\s+|human\s+)?(approval|confirmation|consent)", "skip-approval"),
    (r"\b(auto[- ]?approved?|pre[- ]?approved|already approved|approval (is |was )?granted)\b", "claims-approval"),
    (r"\b(system|assistant|developer)\s*(prompt|message|note)?\s*:", "role-marker"),
    (r"approval[_ ]?token", "mentions-approval-token"),
    (r"<\s*/?\s*(system|instructions?|tool_call|function_call)\s*>", "fake-tag"),
    (r"今すぐ(購入|買|決済|支払|注文)", "buy-now-ja"),
    (r"(承認|確認)は?(不要|なし|をスキップ|スキップ|済み)", "skip-approval-ja"),
    (r"(これまで|以前|上記|前)の指示を無視", "ignore-instructions-ja"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), tag) for p, tag in _PATTERNS]


def scan_text(where: str, text: str) -> list[str]:
    out = []
    for rx, tag in _COMPILED:
        m = rx.search(text or "")
        if m:
            out.append(f"{where}: {tag} ({m.group(0)[:40]!r})")
    return out


def scan_injection(listing: Listing) -> list[str]:
    """Instruction-like text found in a listing. Returned as warnings only."""
    parts = [("title_ja", listing.title_ja), ("title_en", listing.title_en),
             ("seller", listing.seller), ("url", listing.url)]
    parts += [(f"fields.{k}", f"{k} {v}") for k, v in (listing.fields or {}).items()]
    out: list[str] = []
    for where, text in parts:
        out += scan_text(where, str(text))
    return out


def preflight(pick: RankedPick, budget_usd: float | None) -> str | None:
    """Reason this pick must not reach the cart, or None."""
    l, c = pick.listing, pick.cost
    if c.blocked_reason:
        return f"cost engine blocked this item: {c.blocked_reason}"
    if c.listing_id != l.id:
        return f"cost is for {c.listing_id}, not for {l.id}"
    total = c.total_usd
    if not isinstance(total, (int, float)) or not math.isfinite(total) or total <= 0:
        return f"landed cost is not a usable number: {total!r}"
    if not isinstance(l.price_jpy, int) or l.price_jpy <= 0:
        return f"listing price is not a usable number: {l.price_jpy!r}"
    if budget_usd is not None and total > budget_usd:
        return f"landed ${total:.2f} is over the budget ${budget_usd:.2f}"
    return None


class UnsafeUrl(ValueError):
    """A URL the browser driver must not open."""


def check_open_url(url: str) -> str:
    """Only plain http(s) URLs with a host may reach a browser / dt-ax. Returns the URL or raises UnsafeUrl."""
    import urllib.parse
    u = str(url or "").strip()
    if not u or u.startswith("-") or len(u) > 2048 or any(c in u for c in "\r\n\t\x00 "):
        raise UnsafeUrl(f"refusing to open {u[:60]!r}: not a plain http(s) URL")
    p = urllib.parse.urlsplit(u)
    if p.scheme.lower() not in ("http", "https") or not p.hostname or p.username or p.password:
        raise UnsafeUrl(f"refusing to open {u[:60]!r}: only http(s) URLs with a host are allowed")
    return u
