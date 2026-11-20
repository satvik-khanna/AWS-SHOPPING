"""Optional Superlinked SIE rerank for the chat list.

Offline (default) the list keeps the pipeline ranker's order. Only when SHOPPER_LIVE=1 and
SIE_API_KEY (or SIE_BASE_URL) is set does it call SIE through the client already in the repo
(backend/kai/integrations/sie.py). Any failure falls back to the pipeline order and says why,
so the trail always names the engine that ranked the list.
"""
from __future__ import annotations

import asyncio
import os
from typing import Callable

from .. import config
from ..models import RankedPick

# (query, [(id, text)]) -> ({id: score 0..1} | None, engine label)
Scorer = Callable[[str, list[tuple[str, str]]], tuple[dict[str, float] | None, str]]


class _Log:  # the kai client wants an ActivityLog; the API keeps its own trail instead
    def log(self, *a, **k):
        return {}

    def usage(self, *a, **k):
        return None


def sie_configured() -> bool:
    return bool(os.environ.get("SIE_API_KEY") or os.environ.get("SIE_BASE_URL"))


def kai_sie_scorer(query: str, docs: list[tuple[str, str]]) -> tuple[dict[str, float] | None, str]:
    from backend.kai.config import Settings          # imported lazily: needs httpx, live only
    from backend.kai.integrations.sie import SIEAdapter
    adapter = SIEAdapter(Settings.from_env(), _Log())
    return asyncio.run(adapter.rerank(query, docs))


def doc_text(p: RankedPick) -> str:
    l = p.listing
    return f"{l.title_en} / {l.title_ja} / condition {l.condition} / {l.source}"


def rerank(query: str, picks: list[RankedPick], ranker_name: str,
           scorer: Scorer | None = None) -> tuple[list[RankedPick], str]:
    """Return (picks in final order, one trail line naming the engine)."""
    base = f"ranker: {ranker_name} (pipeline order)"
    if not picks:
        return picks, base
    if scorer is None:
        if not config.live():
            return picks, base + "; Superlinked SIE off (offline)"
        if not sie_configured():
            return picks, base + "; Superlinked SIE skipped (SIE_API_KEY not set)"
        scorer = kai_sie_scorer
    try:
        scores, engine = scorer(query, [(p.listing.id, doc_text(p)) for p in picks])
    except Exception as e:  # network down, httpx missing, bad key: keep the pipeline order
        return picks, base + f"; Superlinked SIE failed ({type(e).__name__}: {str(e)[:120]})"
    if not scores:
        return picks, base + f"; Superlinked SIE returned no scores ({engine})"
    order = {p.listing.id: i for i, p in enumerate(picks)}
    out = sorted(picks, key=lambda p: (-scores.get(p.listing.id, -1.0), order[p.listing.id]))
    return out, f"ranker: Superlinked SIE rerank ({engine}) over {ranker_name}"
