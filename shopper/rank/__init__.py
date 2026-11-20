"""Ranking + explanation.

    PriceRanker     base: cheapest-first with condition bonus (kept for comparison / tests)
    SmartRanker     offline: relevance to the query + landed cost + condition + user preferences,
                    one human-readable reason per pick
    default_ranker  what shopper/app.py wires in: SmartRanker -> Superlinked SIE rerank (SHOPPER_LIVE=1 +
                    SIE_API_KEY / SIE_BASE_URL) -> Claude rerank (SHOPPER_LIVE=1 + ANTHROPIC_API_KEY, paid);
                    every live step falls back to the order before it on any error
"""
from __future__ import annotations

from ..models import LandedCost, Listing, Preferences, RankedPick, ShoppingRequest
from .smart import CONDITION_ORDER, SmartRanker, score_parts
from .llm import ClaudeRerank
from .sie import SIERerank

__all__ = ["PriceRanker", "SmartRanker", "ClaudeRerank", "SIERerank", "default_ranker", "score_parts",
           "CONDITION_ORDER"]


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


def default_ranker():
    return ClaudeRerank(base=SIERerank(SmartRanker()))
