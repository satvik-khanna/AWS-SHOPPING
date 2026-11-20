"""Listing search.

    FixtureSource     offline: fixtures/listings.json (raw Japanese rows) -> normalize -> match
    BuyeeLiveSource   SHOPPER_LIVE=1: public Buyee search pages, falls back to fixtures on any error
    ClaudeWebSearchSource  SHOPPER_LIVE=1 + ANTHROPIC_API_KEY: Claude web search finds real listings,
                      falls back to the Buyee source (and so to fixtures) on any error
    default_source()  what shopper/app.py wires in

Both sources run the same pipeline: English query -> Japanese keywords (query.py), raw row ->
Listing with normalized condition / extracted fields / translated title (normalize.py).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import FIXTURES
from ..models import Listing, Preferences, ShoppingRequest
from .normalize import (extract_fields, guess_category, normalize_condition, to_listing,
                        translate_title)
from .query import QueryPlan, expand_query, parse_query, relevance

__all__ = ["FixtureSource", "BuyeeLiveSource", "ClaudeWebSearchSource", "default_source", "expand_query", "parse_query",
           "relevance", "normalize_condition", "extract_fields", "guess_category",
           "translate_title", "to_listing", "QueryPlan"]


class FixtureSource:
    """Offline source. Recall-oriented: returns accessories and junk too, the cost engine and
    ranker decide what is buyable and what is the best pick."""

    def __init__(self, path: Path = FIXTURES / "listings.json"):
        self.path = path
        self.last_status = ""

    def all(self) -> list[Listing]:
        return [to_listing(r) for r in json.loads(self.path.read_text(encoding="utf-8"))]

    def search(self, req: ShoppingRequest, prefs: Preferences) -> list[Listing]:
        plan = parse_query(req.query)
        out = [l for l in self.all() if relevance(plan, l).ok]
        self.last_status = f"fixtures: {len(out)} listings for {plan.ja_keywords or [req.query]}"
        return out


from .buyee import BuyeeLiveSource  # noqa: E402  (needs FixtureSource above)
from .websearch import ClaudeWebSearchSource  # noqa: E402


def default_source():
    """SHOPPER_LIVE=1: Claude web search, then live Buyee search, then fixtures. Offline: fixtures."""
    return ClaudeWebSearchSource(fallback=BuyeeLiveSource(fallback=FixtureSource()))
