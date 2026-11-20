"""Live source: Buyee's public search pages for Mercari and Yahoo! Auctions.

Only used when SHOPPER_LIVE=1. No key, no login, one GET per marketplace. Any failure
(network error, non-200, bot challenge, zero parsed cards) falls back to the fixture source.

Buyee sits behind AWS WAF and answers plain HTTP clients with `202` + `x-amzn-waf-action:
challenge` (seen 2026-11-20). We do not try to get around that: it is reported in `last_status`
and the fixtures are used. Pass `fetch=` to plug in a real-browser fetcher (e.g. the Browserbase
session the checkout module uses) that returns the page HTML for a URL.

Search cards only carry title, price and thumbnail, so condition comes from title wording
(or is assumed 'good' and flagged in fields['condition_source']) and seller is 'unknown'.
"""
from __future__ import annotations

import html
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from ..config import live
from ..models import Listing, Preferences, ShoppingRequest
from .normalize import to_listing
from .query import parse_query, relevance

BASE = "https://buyee.jp"
USER_AGENT = "aws-shopping-hackathon/0.1 (python-urllib; listing search demo)"


class BuyeeError(Exception):
    pass


def mercari_search_url(keyword: str) -> str:
    return f"{BASE}/mercari/search?" + urllib.parse.urlencode({"keyword": keyword, "status": "on_sale"})


def yahoo_search_url(keyword: str) -> str:
    return f"{BASE}/item/search/query/{urllib.parse.quote(keyword)}"


def http_fetch(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            action = r.headers.get("x-amzn-waf-action")
            body = r.read().decode("utf-8", "replace")
            if action or r.status != 200:
                raise BuyeeError(f"HTTP {r.status}" + (f", AWS WAF {action}" if action else ""))
            return body
    except urllib.error.HTTPError as e:
        raise BuyeeError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BuyeeError(f"network: {e}") from e


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _yen(s: str) -> int | None:
    m = re.search(r"([\d,]+)\s*(?:YEN|yen|円)", s)
    return int(m.group(1).replace(",", "")) if m else None


def parse_mercari_search(page: str) -> list[dict]:
    """<ul class="item-lists"><li class="list"><a href="/mercari/item/m123?..."> ... <h2 class="name">,
    <p class="price">4,500 YEN</p> (markup as archived 2026-08-23)."""
    rows = []
    for card in re.split(r'<li class="list">', page)[1:]:
        card = card.split("</li>", 1)[0]
        href = re.search(r'href="/mercari/item/(m\w+)', card)
        name = re.search(r'<h2 class="name">(.*?)</h2>', card, re.S)
        price = re.search(r'<p class="price">(.*?)</p>', card, re.S)
        jpy = _yen(price.group(1)) if price else None
        if not (href and name and jpy):
            continue
        img = re.search(r"imagePath:\s*'([^']+)'", card)
        photo = img.group(1) if img else None
        if photo and photo.startswith("//"):
            photo = "https:" + photo
        rows.append({"id": f"mercari:{href.group(1)}", "source": "mercari",
                     "url": f"https://jp.mercari.com/item/{href.group(1)}",
                     "title_ja": _text(name.group(1)), "price_jpy": jpy,
                     "photos": [photo] if photo else [],
                     "fields": {"buyee_url": f"{BASE}/mercari/item/{href.group(1)}", "price_type": "fixed price"}})
    return rows


_YAHOO_KINDS = {"item/jdirectitems/auction": ("yahoo_auctions", "https://page.auctions.yahoo.co.jp/jp/auction/{id}"),
                "item/yahoo/auction": ("yahoo_auctions", "https://page.auctions.yahoo.co.jp/jp/auction/{id}"),
                "paypayfleamarket/item": ("other", "https://paypayfleamarket.yahoo.co.jp/item/{id}")}


def parse_yahoo_search(page: str) -> list[dict]:
    """<li class="itemCard"> with <div class="itemCard__itemName"><a href="/item/jdirectitems/auction/x123">
    and g-title / g-price pairs ('Current Price', 'Buyout Price', 'Price') (markup as archived 2026-06-11).
    The same page mixes in Yahoo! Flea Market cards (/paypayfleamarket/item/...), kept as source 'other'."""
    rows = []
    for card in re.split(r'<li class="itemCard">', page)[1:]:
        name = re.search(r'<div class="itemCard__itemName">\s*<a href="/(' + "|".join(_YAHOO_KINDS) + r')/(\w+)[^"]*"[^>]*>(.*?)</a>',
                         card, re.S)
        if not name:
            continue
        kind, native, title = name.group(1), name.group(2), _text(name.group(3))
        prices = {_text(k): _yen(v) for k, v in
                  re.findall(r'<span class="g-title">(.*?)</span>.*?<span class="g-price[^"]*">(.*?)</span>', card, re.S)}
        buyout, current = prices.get("Buyout Price"), prices.get("Current Price") or prices.get("Price")
        jpy = buyout or current
        if not (title and jpy):
            continue
        source, native_url = _YAHOO_KINDS[kind]
        img = re.search(r'data-src="([^"]+)"', card)
        fields = {"buyee_url": f"{BASE}/{kind}/{native}"}
        if source == "yahoo_auctions":
            fields["price_type"] = "buy-it-now" if buyout else "auction, current bid (can rise)"
        else:
            fields["price_type"] = "fixed price"
            fields["marketplace"] = "Yahoo! Flea Market"
        rows.append({"id": f"{'yahoo_auctions' if source == 'yahoo_auctions' else 'yahoo_fleamarket'}:{native}",
                     "source": source, "url": native_url.format(id=native), "title_ja": title, "price_jpy": jpy,
                     "photos": [html.unescape(img.group(1))] if img else [], "fields": fields})
    return rows


class BuyeeLiveSource:
    def __init__(self, fallback, fetch: Callable[[str], str] | None = None, max_per_market: int = 30,
                 verbose: bool = True):
        self.fallback = fallback
        self.custom_fetch = fetch is not None
        self.fetch = fetch or http_fetch
        self.max_per_market = max_per_market
        self.verbose = verbose
        self.last_status = ""

    def _note(self, msg: str) -> None:
        self.last_status = msg
        if self.verbose:
            print(f"[listings] {msg}", file=sys.stderr)

    def search(self, req: ShoppingRequest, prefs: Preferences) -> list[Listing]:
        if not live():
            out = self.fallback.search(req, prefs)
            self.last_status = getattr(self.fallback, "last_status", "fixtures")
            return out
        if not self.custom_fetch:
            # No browser fetcher plugged in: Buyee refuses plain HTTP (robots.txt 403, AWS WAF), so use the
            # polite multi-channel live source (Yahoo! Auctions direct, Buyee / Off Mall only if robots allow).
            from .live import LiveSource
            src = LiveSource(self.fallback, verbose=self.verbose)
            out = src.search(req, prefs)
            self.last_status = src.last_status
            return out
        plan = parse_query(req.query)
        keywords = (plan.ja_keywords if plan.alternatives else plan.ja_keywords[:1]) or [req.query]
        keywords = keywords[:2]                 # 'A or B' -> one search per option, at most 2 per market
        keyword = " | ".join(keywords)
        found: list[Listing] = []
        notes: list[str] = []
        jobs = [(m, u(k), f) for k in keywords for m, u, f in (("mercari", mercari_search_url, parse_mercari_search),
                                                               ("yahoo", yahoo_search_url, parse_yahoo_search))]
        for market, url, parse in jobs:
            try:
                rows = parse(self.fetch(url))
                if not rows:
                    raise BuyeeError("no listing cards parsed")
                listings = [to_listing(r) for r in rows[: self.max_per_market]]
                kept = [l for l in listings if relevance(plan, l).ok]
                found += [l for l in kept if l.id not in {x.id for x in found}]
                notes.append(f"{market} {len(kept)}/{len(listings)} relevant")
            except Exception as e:      # any error -> this market contributes nothing
                notes.append(f"{market} failed ({e})")
        if not found:
            out = self.fallback.search(req, prefs)
            self._note(f"live Buyee search for '{keyword}' unusable: {'; '.join(notes)} -> fixtures ({len(out)} listings)")
            return out
        self._note(f"live Buyee search for '{keyword}': {'; '.join(notes)}")
        return found
