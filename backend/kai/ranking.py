"""Rank social drops and marketplace listings together.

Semantic relevance comes from Superlinked SIE (dense embeddings, then a
cross-encoder rerank). Without SIE the relevance term is a lexical n-gram
similarity and is labeled as a fallback. The other terms are deterministic
and explained per candidate."""

from __future__ import annotations

from .integrations.sie import SIEAdapter
from .models import (Availability, Candidate, LandedCost, MemoryRecall, ParsedRequest, RankedCandidate,
                     ScoreBreakdown)
from .observability import ActivityLog
from .textutil import cosine
from .vocab import ATTRIBUTES
from .xdiscovery.storefront import BIDDING_STORE_TYPES

WEIGHTS = {"semantic": 0.30, "preference": 0.25, "budget": 0.20, "availability": 0.15, "trust": 0.10}
_AVAIL_SCORE = {Availability.IN_STOCK.value: 1.0, Availability.PREORDER.value: 0.8,
                Availability.MADE_TO_ORDER.value: 0.75, Availability.LOTTERY.value: 0.35,
                Availability.UNKNOWN.value: 0.3, Availability.SOLD_OUT.value: 0.0}
_PURCHASABLE = {Availability.IN_STOCK.value, Availability.PREORDER.value, Availability.MADE_TO_ORDER.value}
_RESOLVED = {"LISTING_RESOLVED", "MARKETPLACE_API", "FIXTURE"}


def purchase_path(c: Candidate, claimed_sold_out: bool = False) -> tuple[str, str]:
    """Decide what KAI may prepare. Nothing here can place an order."""
    if c.availability == Availability.SOLD_OUT.value:
        return "WATCH_ONLY", "The store listing shows it as sold out."
    if claimed_sold_out:
        return "WATCH_ONLY", "The post says it sold out (not confirmed on a store page)."
    if c.store_type in BIDDING_STORE_TYPES:
        return "MANUAL_ONLY", "Auction listing: a bid is binding, so KAI never drafts one."
    if c.availability == Availability.LOTTERY.value:
        return "MANUAL_ONLY", "Lottery sale: entry must be made by you on the store; winning is not guaranteed."
    if not c.purchase_url:
        return "MANUAL_ONLY", "No product page was resolved; only the announcement is known."
    if c.verification not in _RESOLVED:
        return "MANUAL_ONLY", "The store page could not be read, so price and stock are unconfirmed."
    if c.price_jpy is None:
        return "MANUAL_ONLY", "The listing price could not be read."
    if c.availability not in _PURCHASABLE:
        return "MANUAL_ONLY", "Availability could not be confirmed on the store page."
    return "PROXY_REQUEST_DRAFT", "Listing resolved with price and purchasable status; a nonbinding proxy request can be drafted."


class Ranker:
    def __init__(self, sie: SIEAdapter, log: ActivityLog):
        self.sie = sie
        self.log = log

    async def rank(self, request: ParsedRequest, memory: MemoryRecall, candidates: list[Candidate],
                   costs: dict[str, LandedCost], claimed_sold_out: set[str] | None = None,
                   run_id: str | None = None) -> list[RankedCandidate]:
        if not candidates:
            return []
        claimed_sold_out = claimed_sold_out or set()
        preferred = list(dict.fromkeys(memory.preferred + request.stated_preferences))
        avoided = [a for a in memory.avoided if a not in request.stated_preferences]
        pref_words = " ".join(ATTRIBUTES[a]["terms"][0] for a in preferred if a in ATTRIBUTES)
        query = f"{request.raw} {' '.join(request.ja_terms)} {pref_words}".strip()
        docs = [f"{c.title} {c.description or ''} {' '.join(ATTRIBUTES[a]['label'] for a in c.attributes if a in ATTRIBUTES)}"
                for c in candidates]

        vecs, backend = await self.sie.embed([query] + docs, run_id=run_id)
        sims = [max(0.0, cosine(vecs[0], v)) for v in vecs[1:]]
        top = max(sims) or 1.0
        semantic = [s / top for s in sims]
        rerank, rerank_backend = await self.sie.rerank(query, [(c.id, d) for c, d in zip(candidates, docs)], run_id)
        if rerank:
            semantic = [0.4 * s + 0.6 * rerank.get(c.id, 0.0) for s, c in zip(semantic, candidates)]
            backend = f"{backend} + rerank {rerank_backend}"

        ranked: list[RankedCandidate] = []
        for cand, sem in zip(candidates, semantic):
            reasons: list[str] = []
            # preference
            hits = [a for a in preferred if a in cand.attributes]
            bad = [a for a in avoided if a in cand.attributes]
            if preferred or avoided:
                pref = 0.5 + (0.5 * len(hits) / len(preferred) if preferred else 0.0) - 0.4 * len(bad)
                pref = max(0.0, min(1.0, pref))
                if hits:
                    reasons.append("Matches your preferences: " + ", ".join(ATTRIBUTES[a]["label"] for a in hits))
                if bad:
                    reasons.append("Has something you avoid: " + ", ".join(ATTRIBUTES[a]["label"] for a in bad))
                if preferred and not hits and not bad:
                    reasons.append("No stated preference matched")
            else:
                pref = 0.5
            # budget
            cost = costs.get(cand.id)
            if cost is None or cost.total_usd is None:
                budget = 0.3
                reasons.append("Landed cost unknown (no price)")
            elif request.budget_usd is None:
                budget = 0.7
            elif cost.total_usd <= request.budget_usd:
                budget = 1.0 - 0.2 * (cost.total_usd / request.budget_usd)
                reasons.append(f"Estimated ${cost.total_usd:.2f} delivered, within ${request.budget_usd:.0f}")
            else:
                over = (cost.total_usd - request.budget_usd) / request.budget_usd
                budget = max(0.0, 0.5 - 2 * over)
                reasons.append(f"Estimated ${cost.total_usd:.2f} delivered, over ${request.budget_usd:.0f} by {over:.0%}")
            # availability
            avail = _AVAIL_SCORE.get(cand.availability, 0.3)
            if cand.id in claimed_sold_out:
                avail = 0.0
                reasons.append("Post says it sold out")
            elif cand.availability != Availability.IN_STOCK.value:
                reasons.append(f"Availability: {str(cand.availability).replace('_', ' ').lower()}")
            # trust
            if cand.is_fixture:
                trust = 0.5
                reasons.append("Sample (fixture) data")
            elif cand.verification in ("LISTING_RESOLVED", "MARKETPLACE_API"):
                trust = 1.0 if cand.verification == "LISTING_RESOLVED" else 0.9
            else:
                trust = 0.2
                reasons.append("Announcement only; store listing not verified")
            if cand.injection_flags:
                trust = max(0.0, trust - 0.3)
                reasons.append("Page contained instruction-like text (ignored)")

            total = (WEIGHTS["semantic"] * sem + WEIGHTS["preference"] * pref + WEIGHTS["budget"] * budget
                     + WEIGHTS["availability"] * avail + WEIGHTS["trust"] * trust)
            if avail == 0.0:
                total *= 0.3  # sold-out items stay visible but cannot outrank purchasable ones
            if cost is not None and cost.within_budget == "no":
                total *= 0.7  # the budget is a stated limit, not just one signal among five
            path, why = purchase_path(cand, cand.id in claimed_sold_out)
            ranked.append(RankedCandidate(
                candidate=cand, cost=cost, purchase_path=path, purchase_path_reason=why,
                score=ScoreBreakdown(semantic=round(sem, 3), preference=round(pref, 3), budget=round(budget, 3),
                                     availability=round(avail, 3), trust=round(trust, 3), total=round(total, 4),
                                     ranking_backend=backend, reasons=reasons)))
        ranked.sort(key=lambda r: r.score.total, reverse=True)
        for i, r in enumerate(ranked, 1):
            r.rank = i
        self.log.log("ranking", f"Ranked {len(ranked)} candidate(s) with {backend}. Top: "
                                f"{ranked[0].candidate.title[:50]} ({ranked[0].score.total:.2f})", "ok", run_id,
                     {"backend": backend, "weights": WEIGHTS,
                      "top": [{"title": r.candidate.title[:60], "score": r.score.total, "source": r.candidate.source}
                              for r in ranked[:5]]})
        return ranked
