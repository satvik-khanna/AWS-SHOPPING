"""Optional Claude rerank on top of the offline ranker.

Runs only when SHOPPER_LIVE=1 AND ANTHROPIC_API_KEY is set (paid API: the key holder opts in).
Claude sees the top N offline picks (title, landed cost, condition, fields, offline reason) and
returns an order plus a one-line reason per pick. Any error, timeout or malformed answer -> the
offline order is returned unchanged.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from dataclasses import replace
from typing import Callable

from ..models import LandedCost, Listing, Preferences, RankedPick, ShoppingRequest

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"


def _post(body: dict, key: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


class ClaudeRerank:
    def __init__(self, base, post: Callable[[dict, str], dict] | None = None, top_n: int = 8):
        self.base = base
        self.post = post or _post
        self.top_n = top_n
        self.last_status = ""

    def enabled(self) -> bool:
        return os.environ.get("SHOPPER_LIVE") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))

    def rank(self, req: ShoppingRequest, prefs: Preferences,
             candidates: list[tuple[Listing, LandedCost]]) -> list[RankedPick]:
        picks = self.base.rank(req, prefs, candidates)
        if not self.enabled() or len(picks) < 2:
            self.last_status = getattr(self.base, "last_status", "") or "offline ranking"
            return picks
        try:
            reranked = self._rerank(req, prefs, picks)
            self.last_status = "claude rerank"
            return reranked
        except Exception as e:      # noqa: BLE001 - any failure -> offline order
            self.last_status = f"claude rerank failed ({e}); offline ranking"
            print(f"[rank] {self.last_status}", file=sys.stderr)
            return picks

    def _rerank(self, req: ShoppingRequest, prefs: Preferences, picks: list[RankedPick]) -> list[RankedPick]:
        head, tail = picks[: self.top_n], picks[self.top_n:]
        items = [{"id": p.listing.id, "title_en": p.listing.title_en, "title_ja": p.listing.title_ja,
                  "landed_usd": p.cost.total_usd, "condition": p.listing.condition,
                  "fields": p.listing.fields, "offline_reason": p.reason} for p in head]
        prompt = (
            "You pick the best second-hand listing from Japan for a shopper.\n"
            f"Request: {req.query!r}, budget ${req.max_total_usd} landed, ship to {req.ship_to}.\n"
            f"Preferences: sizes={prefs.sizes} condition_min={prefs.condition_min} notes={prefs.notes}\n"
            f"Candidates (JSON): {json.dumps(items, ensure_ascii=False)}\n"
            'Answer with JSON only: {"order": [{"id": "...", "reason": "one sentence for the shopper"}]} '
            "covering every id, best first. Never invent prices.")
        body = {"model": os.environ.get("SHOPPER_CLAUDE_MODEL", DEFAULT_MODEL), "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}]}
        resp = self.post(body, os.environ["ANTHROPIC_API_KEY"])
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        order = json.loads(m.group(0))["order"] if m else []
        by_id = {p.listing.id: p for p in head}
        out, seen = [], set()
        for o in order:
            p = by_id.get(o.get("id"))
            if p and p.listing.id not in seen:
                seen.add(p.listing.id)
                out.append(replace(p, reason=f"{o.get('reason', '').strip()} [Claude] | {p.reason}"))
        if len(out) != len(head):
            raise ValueError(f"answer covered {len(out)}/{len(head)} ids")
        n = len(out)
        out = [replace(p, score=round(1.0 + (n - i) / n, 4)) for i, p in enumerate(out)]
        return out + tail
