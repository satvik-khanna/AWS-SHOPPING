"""Ranking + explanation. Base: cheapest-first with condition bonus.
TODO(rank pane): Superlinked rerank + Claude API final pick/explanation behind SHOPPER_LIVE."""
from __future__ import annotations

from ..models import LandedCost, Listing, Preferences, RankedPick, ShoppingRequest

CONDITION_ORDER = ["junk", "fair", "good", "like_new", "new"]


class PriceRanker:
    def rank(self, req: ShoppingRequest, prefs: Preferences,
             candidates: list[tuple[Listing, LandedCost]]) -> list[RankedPick]:
        floor = req.condition_min or prefs.condition_min
        picks = []
        for l, c in candidates:
            cond = CONDITION_ORDER.index(l.condition) if l.condition in CONDITION_ORDER else 0
            if floor and floor in CONDITION_ORDER and cond < CONDITION_ORDER.index(floor):
                continue
            headroom = max(req.max_total_usd - c.total_usd, 0) / req.max_total_usd
            score = round(headroom + 0.1 * cond, 4)
            picks.append(RankedPick(l, c, score,
                                    f"${c.total_usd} landed (${req.max_total_usd - c.total_usd:.0f} under budget), condition {l.condition}"))
        return sorted(picks, key=lambda p: p.score, reverse=True)
