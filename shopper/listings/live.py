"""LiveSource: real marketplace listings when SHOPPER_LIVE=1, fixtures (labelled) otherwise.

Channels, each through PoliteFetcher (robots.txt, 1 request/s, no evasion, no sign-in):
  yahoo    auctions.yahoo.co.jp/search/search?p=   real titles, prices, condition codes, images
  buyee    buyee.jp Mercari / Yahoo search pages   robots.txt answers 403 -> never crawled
  hardoff  netmall.hardoff.co.jp/search/           Disallow: /search/ in robots.txt -> never crawled
  mercari  jp.mercari.com                          search is rendered client-side and the data API
                                                   (/v1/, /v2/) is disallowed -> not searched
If no channel returns a relevant live listing, the fixture source is used and every listing says
fields['data_origin'] = 'fixture' (invented sample, not a real item). last_status explains which.
"""
from __future__ import annotations

import sys
import urllib.parse

from ..config import live
from ..models import Listing, Preferences, ShoppingRequest
from . import yahoo
from .buyee import mercari_search_url, parse_mercari_search, parse_yahoo_search, yahoo_search_url
from .normalize import to_listing
from .polite import FetchError, PoliteFetcher, RobotsDisallowed, now_iso
from .query import parse_query, relevance

HARDOFF_SEARCH = "https://netmall.hardoff.co.jp/search/?"
MERCARI_NOTE = ("mercari not searched (jp.mercari.com renders search in the browser and its data API "
                "/v1/ /v2/ is disallowed by robots.txt)")


class LiveSource:
    def __init__(self, fallback, fetcher: PoliteFetcher | None = None, max_per_query: int = 50,
                 verbose: bool = True, channels: tuple[str, ...] = ("yahoo", "buyee", "hardoff")):
        self.fallback = fallback
        self.fetcher = fetcher or PoliteFetcher()
        self.max_per_query = max_per_query
        self.verbose = verbose
        self.channels = channels
        self.last_status = ""
        self.notes: list[str] = []

    def _note(self, msg: str) -> None:
        self.last_status = msg
        if self.verbose:
            print(f"[listings] {msg}", file=sys.stderr)

    def _rows(self, channel: str, keyword: str) -> list[dict]:
        f = self.fetcher
        if channel == "yahoo":
            url = yahoo.search_url(keyword)
            final, page = f.fetch(url)
            return yahoo.parse_search(page, fetched_from=final)
        if channel == "buyee":
            rows = []
            for url, parse in ((mercari_search_url(keyword), parse_mercari_search),
                               (yahoo_search_url(keyword), parse_yahoo_search)):
                final, page = f.fetch(url)
                at = now_iso()
                for r in parse(page):
                    r.setdefault("fields", {}).update({"data_origin": "live", "fetched_at": at, "fetched_from": final})
                    rows.append(r)
            return rows
        if channel == "hardoff":
            url = HARDOFF_SEARCH + urllib.parse.urlencode({"q": keyword})
            ok, why = f.allowed(url)
            if not ok:
                raise RobotsDisallowed(f"{url}: {why}")
            raise FetchError("no Off Mall parser yet")      # not reached while robots.txt disallows /search/
        raise ValueError(channel)

    def search(self, req: ShoppingRequest, prefs: Preferences) -> list[Listing]:
        if not live():
            out = self.fallback.search(req, prefs)
            self.last_status = getattr(self.fallback, "last_status", "fixtures")
            return out
        plan = parse_query(req.query)
        keywords = ((plan.ja_keywords if plan.alternatives else plan.ja_keywords[:1]) or [req.query])[:2]
        found: dict[str, Listing] = {}
        notes: list[str] = []
        for channel in self.channels:
            for kw in keywords:
                try:
                    rows = self._rows(channel, kw)[: self.max_per_query]
                    listings = [to_listing(r) for r in rows]
                    kept = [l for l in listings if relevance(plan, l).ok]
                    for l in kept:
                        found.setdefault(l.id, l)
                    notes.append(f"{channel} '{kw}': {len(kept)}/{len(listings)} relevant")
                except RobotsDisallowed as e:
                    notes.append(f"{channel} not crawled ({e})")
                    break                                   # same answer for every keyword
                except Exception as e:                      # noqa: BLE001 - any failure -> next channel
                    notes.append(f"{channel} '{kw}' failed ({e})")
        notes.append(MERCARI_NOTE)
        self.notes = notes
        if not found:
            out = self.fallback.search(req, prefs)
            self._note("LIVE SEARCH RETURNED NOTHING -> showing FIXTURE samples (not real items): " + "; ".join(notes))
            return out
        self._note(f"live: {len(found)} real listings; " + "; ".join(notes))
        return list(found.values())
