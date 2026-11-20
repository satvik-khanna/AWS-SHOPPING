"""Optional Superlinked SIE cross-encoder rerank on top of the offline ranker.

Runs only when SHOPPER_LIVE=1 and SIE_API_KEY or SIE_BASE_URL is set. Reuses the SIE client that
lives in backend/kai/integrations/sie.py (POST /v1/score/<rerank model>); that client needs httpx,
so when httpx or the backend package is missing, or SIE errors, the offline order is kept and
last_status says why. Final score = 0.7 * offline score + 0.3 * SIE score (0..1).

Not covered by SIE here: JA->EN translation and field extraction stay in shopper/listings
(lexicon + regex); the SIE client in main only exposes encode / score.
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import replace
from typing import Awaitable, Callable

from ..config import ROOT
from ..models import LandedCost, Listing, Preferences, RankedPick, ShoppingRequest

W_SIE = 0.3
ScoreFn = Callable[[str, list[tuple[str, str]]], Awaitable[tuple[dict[str, float] | None, str]]]


class _Log:                                    # duck-typed stand-in for kai.observability.ActivityLog
    def usage(self, *a, **k) -> None: ...
    def log(self, *a, **k) -> None: ...


def _backend_rerank() -> ScoreFn:
    backend = str(ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from kai.config import Settings                      # noqa: E402  (needs httpx via the adapter)
    from kai.integrations.sie import SIEAdapter          # noqa: E402
    adapter = SIEAdapter(Settings.from_env(), _Log())
    return adapter.rerank


def doc_text(l: Listing) -> str:
    specs = ", ".join(f"{k}: {v}" for k, v in l.fields.items()
                      if k not in ("fetched_from", "fetched_at", "data_origin", "data_note", "buyee_url"))
    return f"{l.title_en} | {l.title_ja} | condition {l.condition} | {specs}"


class SIERerank:
    def __init__(self, base, score: ScoreFn | None = None, top_n: int = 20):
        self.base = base
        self._score = score
        self.top_n = top_n
        self.last_status = ""

    def enabled(self) -> bool:
        return os.environ.get("SHOPPER_LIVE") == "1" and bool(
            os.environ.get("SIE_API_KEY") or os.environ.get("SIE_BASE_URL"))

    def rank(self, req: ShoppingRequest, prefs: Preferences,
             candidates: list[tuple[Listing, LandedCost]]) -> list[RankedPick]:
        picks = self.base.rank(req, prefs, candidates)
        if not self.enabled() or len(picks) < 2:
            self.last_status = getattr(self.base, "last_status", "") or "offline ranking"
            return picks
        try:
            score = self._score or _backend_rerank()
            head, tail = picks[: self.top_n], picks[self.top_n:]
            scores, backend = asyncio.run(score(req.query, [(p.listing.id, doc_text(p.listing)) for p in head]))
            if not scores:
                raise RuntimeError(f"no scores ({backend})")
            out = [replace(p, score=round((1 - W_SIE) * p.score + W_SIE * scores.get(p.listing.id, 0.0), 4),
                           reason=f"{p.reason} [Superlinked SIE relevance {scores.get(p.listing.id, 0.0):.2f}]")
                   for p in head]
            out.sort(key=lambda p: p.score, reverse=True)
            self.last_status = f"superlinked sie rerank ({backend})"
            return out + tail
        except Exception as e:      # noqa: BLE001 - any failure -> offline order
            self.last_status = f"superlinked sie rerank unavailable ({type(e).__name__}: {e}); offline ranking"
            print(f"[rank] {self.last_status}", file=sys.stderr)
            return picks
