"""request -> recall -> search -> landed cost -> rank -> fill cart (stop for approval)."""
from __future__ import annotations

from dataclasses import dataclass
import inspect

from .interfaces import Checkout, CostEngine, ListingSource, MemoryStore, Ranker
from .models import PipelineResult, ShoppingRequest


@dataclass
class Deps:
    memory: MemoryStore
    source: ListingSource
    cost: CostEngine
    ranker: Ranker
    checkout: Checkout


def run(req: ShoppingRequest, deps: Deps, top_k: int = 3) -> PipelineResult:
    trail: list[str] = []
    prefs = deps.memory.recall(req.user_id)
    trail.append(f"recalled preferences for {req.user_id}: "
                 f"{len(prefs.past_purchases)} past purchases, {len(prefs.rejected_sellers)} rejected sellers")

    listings = [l for l in deps.source.search(req, prefs) if l.seller not in prefs.rejected_sellers]
    src_status = getattr(deps.source, "last_status", "")
    if src_status:
        trail.append(f"source: {src_status}")
    trail.append(f"found {len(listings)} listings")

    candidates = []
    for l in listings:
        c = deps.cost.landed_cost(l, req)
        if c.blocked_reason:
            trail.append(f"skip {l.id}: {c.blocked_reason}")
            continue
        if c.total_usd > req.max_total_usd:
            trail.append(f"skip {l.id}: landed ${c.total_usd} > budget ${req.max_total_usd}")
            continue
        candidates.append((l, c))

    picks = deps.ranker.rank(req, prefs, candidates)[:top_k]
    rank_status = getattr(deps.ranker, "last_status", "")
    if rank_status:
        trail.append(f"ranker: {rank_status}")
    for p in picks:
        trail.append(f"pick {p.listing.id} ${p.cost.total_usd} score={p.score:.2f}: {p.reason}")

    cart = None
    if picks:
        sig = inspect.signature(deps.checkout.fill_cart)
        kw = {}
        if "budget_usd" in sig.parameters:
            kw["budget_usd"] = req.max_total_usd
        if "user_id" in sig.parameters:
            kw["user_id"] = req.user_id
        cart = deps.checkout.fill_cart(picks[0], **kw)
        trail.append(f"cart {cart.status} for {cart.listing_id}")
    else:
        trail.append("no candidate within budget")
    return PipelineResult(request=req, preferences=prefs, picks=picks, cart=cart, trail=trail)
