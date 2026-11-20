"""Live Yahoo! Auctions search, read straight from auctions.yahoo.co.jp (no proxy, no key).

The public search page `/search/search?p=<keyword>` (allowed by robots.txt for this path and
parameter, checked 2026-11-20) is server-rendered by Next.js: every result card is in the
`__NEXT_DATA__` JSON under props.pageProps.initialState.search.items.listing.items. The same
results include Yahoo! Flea Market items (isFleamarketItem), kept as source 'yahoo_fleamarket'.

Each row carries fields['data_origin'] = 'live', fields['fetched_at'] and fields['fetched_from'].
"""
from __future__ import annotations

import json
import re
import urllib.parse

from .polite import now_iso

SEARCH = "https://auctions.yahoo.co.jp/search/search?"
AUCTION_URL = "https://auctions.yahoo.co.jp/jp/auction/{id}"
FLEA_URL = "https://paypayfleamarket.yahoo.co.jp/item/{id}"

# Yahoo's itemCondition codes, written back as the Japanese grade text shown on the item page
CONDITION_CODES = {"NEW": "新品、未使用", "USED10": "未使用に近い", "USED20": "目立った傷や汚れなし",
                   "USED40": "やや傷や汚れあり", "USED60": "傷や汚れあり", "USED80": "全体的に状態が悪い"}
_NEXT = re.compile(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S)


class ParseError(Exception):
    pass


def search_url(keyword: str) -> str:
    return SEARCH + urllib.parse.urlencode({"p": keyword})


def _items(data: dict) -> list[dict]:
    items = (((data.get("props") or {}).get("pageProps") or {}).get("initialState") or {}) \
        .get("search", {}).get("items", {})
    out, seen = [], set()
    for group in ("listing", "featuredListing"):
        for it in (items.get(group) or {}).get("items") or []:
            if isinstance(it, dict) and it.get("auctionId") and it["auctionId"] not in seen:
                seen.add(it["auctionId"])
                out.append(it)
    return out


def parse_search(page: str, fetched_from: str, fetched_at: str | None = None) -> list[dict]:
    """Real search page HTML -> raw rows for normalize.to_listing."""
    m = _NEXT.search(page)
    if not m:
        raise ParseError("no __NEXT_DATA__ on the page (layout changed or not a search page)")
    try:
        data = json.loads(m.group(1))
    except ValueError as e:
        raise ParseError(f"__NEXT_DATA__ is not JSON: {e}") from e
    fetched_at = fetched_at or now_iso()
    rows = []
    for it in _items(data):
        aid, title, price = str(it["auctionId"]), str(it.get("title") or "").strip(), it.get("price")
        if not title or not isinstance(price, (int, float)) or price <= 0:
            continue
        flea = bool(it.get("isFleamarketItem"))
        seller = it.get("seller") or {}
        name = str(seller.get("displayName") or "")
        seller_id = name if name and set(name) != {"*"} else f"yahoo-user-{seller.get('userId') or 'unknown'}"
        path = " > ".join(c.get("name", "") for c in it.get("categoryPath") or [] if c.get("id"))
        fixed = bool(it.get("isFixedPrice"))
        fields = {
            "data_origin": "live",
            "fetched_at": fetched_at,
            "fetched_from": fetched_from,
            "price_type": "fixed price" if fixed or flea else "auction, current bid (can rise)",
        }
        if it.get("itemCondition"):
            fields["condition_code"] = str(it["itemCondition"])
        if path:
            fields["yahoo_category"] = path
        if not fixed and it.get("buyNowPrice"):
            fields["buy_now_jpy"] = str(it["buyNowPrice"])
        if it.get("bidCount") is not None and not flea:
            fields["bid_count"] = str(it["bidCount"])
        if it.get("endTime"):
            fields["end_time"] = str(it["endTime"])
        if it.get("isFreeShipping"):
            fields["domestic_shipping"] = "seller pays"
        elif it.get("currentPriceShippingFee"):
            fields["domestic_shipping_jpy"] = str(it["currentPriceShippingFee"])
        if seller.get("goodRating"):
            fields["seller_rating"] = str(seller["goodRating"])
        if seller.get("isStore"):
            fields["seller_type"] = "store"
        if flea:
            fields["marketplace"] = "Yahoo! Flea Market"
        rows.append({
            "id": f"{'yahoo_fleamarket' if flea else 'yahoo_auctions'}:{aid}",
            "source": "yahoo_fleamarket" if flea else "yahoo_auctions",
            "url": (FLEA_URL if flea else AUCTION_URL).format(id=aid),
            "title_ja": title,
            "description_ja": path,
            "condition_ja": CONDITION_CODES.get(str(it.get("itemCondition") or "")),
            "price_jpy": int(price),
            "seller": seller_id,
            "ships_abroad": False,
            "photos": [it["imageUrl"]] if it.get("imageUrl") else [],
            "fields": fields,
        })
    return rows
