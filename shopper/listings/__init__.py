"""Listing search. Base: offline fixture source. TODO(listings pane): real Mercari / Yahoo Auctions
search + Japanese->English extraction (Superlinked SIE adapter behind SHOPPER_LIVE)."""
from __future__ import annotations

import json
from pathlib import Path

from ..config import FIXTURES
from ..models import Listing, Preferences, ShoppingRequest


class FixtureSource:
    def __init__(self, path: Path = FIXTURES / "listings.json"):
        self.path = path

    def search(self, req: ShoppingRequest, prefs: Preferences) -> list[Listing]:
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        words = [w for w in req.query.lower().split() if len(w) > 2]
        out = []
        for r in rows:
            hay = (r["title_en"] + " " + r.get("category", "")).lower()
            if not words or any(w in hay for w in words):
                out.append(Listing(**r))
        return out
