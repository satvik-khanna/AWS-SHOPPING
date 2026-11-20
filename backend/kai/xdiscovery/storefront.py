"""Trace an announcement to the store that actually sells the product.

A post URL is never treated as a purchase URL. Links are classified, link
hubs are expanded one level, and product pages are parsed for price,
availability and restrictions. Everything on a fetched page is untrusted
data: it is parsed into fields, scanned for instruction-like text, and never
executed or obeyed."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment

from ..models import Availability, Evidence, ExternalLink, StorefrontResolution
from ..net import FIXTURE_HOST_SUFFIX, FetchBudget, SafeFetcher
from ..observability import ActivityLog
from ..security import detect_injection, sanitize_untrusted
from ..textutil import nfkc, ngram_containment, squash
from ..vocab import detect_attributes

# (store type, host suffixes, product path pattern)
_STORES: list[tuple[str, tuple[str, ...], str]] = [
    ("booth", ("booth.pm",), r"/items/\d+"),
    ("base", ("thebase.in", "base.shop", "base.ec", "buyshop.jp", "theshop.jp", "shopselect.net", "official.ec",
              "handcrafted.jp", "thebase.com"), r"/items/\d+"),
    ("stores", ("stores.jp",), r"/items/[0-9a-f]{8,}"),
    ("shopify", ("myshopify.com",), r"/products/[^/]+"),
    ("minne", ("minne.com",), r"/items/\d+"),
    ("creema", ("creema.jp",), r"/item/\d+"),
    ("suzuri", ("suzuri.jp",), r"/[^/]+/\d+/"),
    ("mercari_shops", ("mercari-shops.com",), r"/products/"),
    ("mercari", ("mercari.com",), r"/item/m\d+|/shops/product/"),
    ("yahoo_auctions", ("auctions.yahoo.co.jp",), r"/auction/"),
    ("yahoo_shopping", ("shopping.yahoo.co.jp",), r"\.html$"),
    ("rakuten", ("rakuten.co.jp",), r"^/[^/]+/[^/]+"),
    ("amazon_jp", ("amazon.co.jp",), r"/dp/|/gp/product/"),
]
_LINK_HUBS = ("linktr.ee", "beacons.ai", "lit.link", "potofu.me", "bio.site", "linkin.bio", "profcard.info",
              "carrd.co", "taplink.cc", "lnk.bio", "bento.me")
_SOCIAL = ("x.com", "twitter.com", "instagram.com", "youtube.com", "youtu.be", "tiktok.com", "facebook.com",
           "pixiv.net", "bsky.app", "threads.net", "misskey.io", "note.com", "twitch.tv", "discord.gg",
           "discord.com", "line.me", "threads.com")
_MEDIA_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov")

# Auctions are never drafted: placing a bid is a binding action.
BIDDING_STORE_TYPES = {"yahoo_auctions"}

STORE_LABELS = {
    "booth": "BOOTH", "base": "BASE", "stores": "STORES", "shopify": "Shopify store", "minne": "minne",
    "creema": "Creema", "suzuri": "SUZURI", "mercari": "Mercari", "mercari_shops": "Mercari Shops",
    "yahoo_auctions": "Yahoo! Auctions", "yahoo_shopping": "Yahoo! Shopping", "rakuten": "Rakuten Ichiba",
    "amazon_jp": "Amazon.co.jp", "independent": "Independent store",
}

_SCHEMA_AVAIL = {
    "instock": Availability.IN_STOCK, "limitedavailability": Availability.IN_STOCK,
    "onlineonly": Availability.IN_STOCK, "instoreonly": Availability.IN_STOCK,
    "outofstock": Availability.SOLD_OUT, "soldout": Availability.SOLD_OUT, "discontinued": Availability.SOLD_OUT,
    "preorder": Availability.PREORDER, "presale": Availability.PREORDER,
    "backorder": Availability.MADE_TO_ORDER, "madetoorder": Availability.MADE_TO_ORDER,
    "oos": Availability.SOLD_OUT, "in stock": Availability.IN_STOCK, "out of stock": Availability.SOLD_OUT,
}
_SOLD_OUT_TEXT = re.compile(r"SOLD\s?OUT|売り切れ|売切れ|完売|在庫なし|在庫切れ|品切れ|入荷待ち", re.I)
_LOTTERY_TEXT = re.compile(r"抽選(販売|受付|申込|応募|エントリー)|lottery (sale|entry)", re.I)
_PREORDER_TEXT = re.compile(r"予約(商品|販売|受付|注文)|pre-?order", re.I)
_MTO_TEXT = re.compile(r"受注(生産|販売|制作)|オーダーメイド|made[- ]to[- ]order", re.I)
_CART_TEXT = re.compile(r"カートに(入れる|追加)|カートへ|購入する|購入手続き|add to cart|buy now", re.I)
_RESTRICTIONS: list[tuple[str, re.Pattern[str]]] = [
    ("Purchase limit per person", re.compile(r"(お一人様|おひとり様|1人|一人)\s*\d*\s*(点|個|セット)(まで|限り)?")),
    ("No overseas shipping", re.compile(r"海外発送(は)?(不可|できません|行っておりません|非対応)|国内発送のみ|日本国内(のみ|限定)|Japan only", re.I)),
    ("Resale prohibited", re.compile(r"転売(目的)?(の購入)?(は)?(禁止|お断り|ご遠慮)")),
    ("Age-restricted", re.compile(r"R-?18|18歳未満|成人向け", re.I)),
    ("Membership or login required", re.compile(r"会員限定|ログインが必要|フォロワー限定")),
    ("No cancellations or returns", re.compile(r"キャンセル(不可|できません)|返品(不可|できません)")),
]
_INTL_YES = re.compile(r"海外発送(対応|可能|可|OK)|international shipping|ships? worldwide|worldwide shipping|overseas shipping available", re.I)
_PAGE_PRICE = re.compile(r"[¥￥]\s*([0-9][0-9,]{2,})|([0-9][0-9,]{2,})\s*円")


@dataclass
class LinkClass:
    kind: str  # STOREFRONT | LINK_HUB | SOCIAL | MEDIA | SHORTENER | OTHER
    store_type: str | None = None
    is_product_page: bool = False


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def classify_url(url: str) -> LinkClass:
    try:
        parts = urlsplit(url)
    except ValueError:
        return LinkClass("OTHER")
    host = (parts.hostname or "").lower()
    if host.endswith(FIXTURE_HOST_SUFFIX):  # sample hosts mirror a real host shape
        host = host[: -len(FIXTURE_HOST_SUFFIX)]
    path = parts.path or "/"
    if not host:
        return LinkClass("OTHER")
    if host == "t.co" or host in {"bit.ly", "buff.ly", "ow.ly", "tinyurl.com"}:
        return LinkClass("SHORTENER")
    if path.lower().endswith(_MEDIA_EXT):
        return LinkClass("MEDIA")
    if _host_matches(host, _SOCIAL):
        return LinkClass("SOCIAL")
    if _host_matches(host, _LINK_HUBS):
        return LinkClass("LINK_HUB")
    for store_type, suffixes, product_pat in _STORES:
        if _host_matches(host, suffixes):
            return LinkClass("STOREFRONT", store_type, bool(re.search(product_pat, path)))
    # Unknown host: a Shopify-style or BASE-style product path suggests an independent store.
    if re.search(r"/products/[^/]+|/items?/[\w-]+|/shop/[\w-]+|/product/[\w-]+", path):
        return LinkClass("STOREFRONT", "independent", True)
    return LinkClass("OTHER")


def annotate_link(link: ExternalLink) -> ExternalLink:
    c = classify_url(link.target)
    link.kind = "OTHER" if c.kind == "SHORTENER" else c.kind
    link.store_type = c.store_type
    link.is_product_page = c.is_product_page
    return link


# --------------------------------------------------------------------------
# Page parsing
# --------------------------------------------------------------------------


def _jsonld_products(soup: BeautifulSoup) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            t = node.get("@type")
            types = t if isinstance(t, list) else [t]
            if any(isinstance(x, str) and x.lower() in {"product", "productgroup"} for x in types):
                found.append(node)
            for key in ("@graph", "mainEntity", "hasVariant"):
                if key in node:
                    walk(node[key])

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        try:
            walk(json.loads(raw.strip()))
        except (json.JSONDecodeError, TypeError):
            continue
    return found


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return None


def _first_offer(offers: Any) -> dict[str, Any]:
    if isinstance(offers, list):
        offers = next((o for o in offers if isinstance(o, dict)), {})
    if not isinstance(offers, dict):
        return {}
    if offers.get("price") is None and offers.get("lowPrice") is not None:
        offers = {**offers, "price": offers["lowPrice"]}
    if offers.get("price") is None and isinstance(offers.get("offers"), list) and offers["offers"]:
        inner = _first_offer(offers["offers"])
        offers = {**inner, **{k: v for k, v in offers.items() if k != "offers" and v is not None}}
    return offers


def _to_number(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _image_urls(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [v["url"]] if isinstance(v.get("url"), str) else []
    if isinstance(v, list):
        out: list[str] = []
        for x in v:
            out.extend(_image_urls(x))
        return out
    return []


@dataclass
class ParsedPage:
    is_product: bool
    data: StorefrontResolution
    outbound: list[str]
    same_host_links: list[tuple[str, str]]  # (url, anchor text)


def parse_page(html: str, url: str, store_type: str | None) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    res = StorefrontResolution(status="UNSUPPORTED", store_type=store_type)
    ev: list[Evidence] = []

    # Hidden text is included on purpose so concealed instructions get flagged.
    comments = " ".join(str(c) for c in soup.find_all(string=lambda s: isinstance(s, Comment)))
    for tag in soup(["script", "style", "noscript", "template"]):
        if tag.get("type") != "application/ld+json":
            tag.extract()
    products = _jsonld_products(soup)
    for tag in soup(["script"]):
        tag.extract()
    visible = sanitize_untrusted(squash(nfkc(soup.get_text(" "))), 20000)
    meta_desc = _meta(soup, "og:description", "description") or ""
    res.injection_flags = sorted(set(detect_injection(visible) + detect_injection(comments) + detect_injection(meta_desc)))

    if "cdn.shopify.com" in html or "Shopify.shop" in html or "myshopify.com" in html:
        res.store_type = "shopify" if store_type in (None, "independent") else store_type

    title = None
    price: float | None = None
    currency: str | None = None
    avail_raw: str | None = None
    description: str | None = None

    if products:
        p = products[0]
        title = p.get("name") if isinstance(p.get("name"), str) else None
        description = p.get("description") if isinstance(p.get("description"), str) else None
        res.image_urls = _image_urls(p.get("image"))[:4]
        offer = _first_offer(p.get("offers"))
        price = _to_number(offer.get("price"))
        currency = offer.get("priceCurrency") if isinstance(offer.get("priceCurrency"), str) else None
        if isinstance(offer.get("availability"), str):
            avail_raw = offer["availability"]
        seller = offer.get("seller") or p.get("brand")
        if isinstance(seller, dict) and isinstance(seller.get("name"), str):
            res.store_name = seller["name"]
        ev.append(Evidence(field="structured_data", snippet="schema.org Product found in page JSON-LD", source="json-ld"))

    og_type = (_meta(soup, "og:type") or "").lower()
    if title is None:
        title = _meta(soup, "og:title")
    if price is None:
        price = _to_number(_meta(soup, "product:price:amount", "og:price:amount"))
        if price is not None:
            currency = currency or _meta(soup, "product:price:currency", "og:price:currency")
            ev.append(Evidence(field="price", snippet=f"meta price {price:g} {currency or ''}".strip(), source="meta tags"))
    if avail_raw is None:
        avail_raw = _meta(soup, "product:availability", "og:availability")
    if not res.image_urls:
        img = _meta(soup, "og:image")
        if img:
            res.image_urls = [img]
    res.store_name = res.store_name or _meta(soup, "og:site_name")
    if title is None and soup.title and soup.title.string:
        title = soup.title.string
    description = description or meta_desc or None

    res.product_title = sanitize_untrusted(squash(title or ""), 200) or None
    res.description_excerpt = sanitize_untrusted(squash(description or ""), 400) or None

    # ---- price ----------------------------------------------------------
    looks_product = bool(products) or "product" in og_type
    if price is not None:
        res.price_original = price
        res.currency = (currency or "").upper() or None
        if res.currency == "JPY":
            res.price_jpy = int(round(price))
            ev.append(Evidence(field="price", snippet=f"{res.price_jpy:,} JPY", source="structured data"))
        elif res.currency is None:
            ev.append(Evidence(field="price", snippet=f"{price:g} (currency not stated on page)", source="structured data"))
    elif looks_product or (store_type and store_type != "independent"):
        m = _PAGE_PRICE.search(visible)
        if m:
            res.price_jpy = int((m.group(1) or m.group(2)).replace(",", ""))
            res.currency = "JPY"
            ev.append(Evidence(field="price", snippet=visible[max(0, m.start() - 20): m.end() + 10],
                               source="page text (first yen amount; lower confidence)"))

    # ---- availability ---------------------------------------------------
    body_for_signals = " ".join(x for x in [res.product_title, res.description_excerpt, visible] if x)
    if avail_raw:
        key = avail_raw.rsplit("/", 1)[-1].strip().lower()
        res.availability = _SCHEMA_AVAIL.get(key, Availability.UNKNOWN)
        res.availability_source = f"structured data ({avail_raw.rsplit('/', 1)[-1]})"
    else:
        if _SOLD_OUT_TEXT.search(body_for_signals) and not _CART_TEXT.search(body_for_signals):
            res.availability = Availability.SOLD_OUT
            res.availability_source = "page text (lower confidence)"
        elif _CART_TEXT.search(body_for_signals):
            res.availability = Availability.IN_STOCK
            res.availability_source = "page text: purchase button wording (lower confidence)"
    # Sale mode refines "in stock": a lottery or preorder page is not immediately purchasable stock.
    if res.availability in (Availability.IN_STOCK, Availability.UNKNOWN):
        for pat, value, label in ((_LOTTERY_TEXT, Availability.LOTTERY, "lottery wording"),
                                  (_PREORDER_TEXT, Availability.PREORDER, "preorder wording"),
                                  (_MTO_TEXT, Availability.MADE_TO_ORDER, "made-to-order wording")):
            m = pat.search(body_for_signals)
            if m:
                res.availability = value
                res.availability_source = f"{res.availability_source + '; ' if res.availability_source else ''}page text: {label}"
                ev.append(Evidence(field="availability", snippet=body_for_signals[max(0, m.start() - 15): m.end() + 15],
                                   source="page text"))
                break

    # ---- restrictions / destination ------------------------------------
    for label, pat in _RESTRICTIONS:
        m = pat.search(visible)
        if m:
            res.restrictions.append(label)
            ev.append(Evidence(field="restriction", snippet=visible[max(0, m.start() - 10): m.end() + 10], source="page text"))
    if "No overseas shipping" in res.restrictions:
        res.ships_internationally = "no"
    elif _INTL_YES.search(visible):
        res.ships_internationally = "yes"
    res.attributes = detect_attributes(" ".join(x for x in [res.product_title, res.description_excerpt] if x))
    res.evidence = ev

    # ---- links ----------------------------------------------------------
    host = (urlsplit(url).hostname or "").lower()
    outbound: list[str] = []
    same: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, str(a["href"]))
        if not href.startswith("http"):
            continue
        text = squash(a.get_text(" ") or "")
        if not text:
            img = a.find("img")
            text = str(img.get("alt") or "") if img else ""
        h = (urlsplit(href).hostname or "").lower()
        if h == host:
            same.append((href.split("#")[0], text))
        elif href not in outbound:
            outbound.append(href)

    is_product = looks_product and bool(res.product_title) and (res.price_original is not None or bool(products))
    return ParsedPage(is_product=is_product, data=res, outbound=outbound[:60], same_host_links=same[:200])


# --------------------------------------------------------------------------
# Tracer
# --------------------------------------------------------------------------

_STATUS_RANK = {"RESOLVED_PRODUCT": 5, "STORE_ONLY": 4, "BLOCKED": 3, "UNSUPPORTED": 2, "ERROR": 1, "NO_LINK": 0}


class StorefrontTracer:
    MAX_LINKS_PER_LEAD = 3
    MAX_DEPTH = 2  # link hub -> store top -> product page

    def __init__(self, fetcher: SafeFetcher, log: ActivityLog):
        self.fetcher = fetcher
        self.log = log

    async def trace(self, links: list[ExternalLink], product_name: str | None, budget: FetchBudget,
                    run_id: str | None = None) -> StorefrontResolution:
        order = {"STOREFRONT": 0, "LINK_HUB": 1, "OTHER": 2}
        cands = [l for l in links if l.kind in order]
        cands.sort(key=lambda l: (order[l.kind], 0 if l.is_product_page else 1, 1 if l.source == "api_profile" else 0))
        if not cands:
            return StorefrontResolution(status="NO_LINK", error="The post has no link to a store or link page.")
        best: StorefrontResolution | None = None
        for link in cands[: self.MAX_LINKS_PER_LEAD]:
            res = await self._resolve(link.target, product_name, budget, 0, [])
            res.requested_url = res.requested_url or link.target
            if res.status == "RESOLVED_PRODUCT":
                return res
            if best is None or _STATUS_RANK[res.status] > _STATUS_RANK[best.status]:
                best = res
        assert best is not None
        return best

    async def resolve_url(self, url: str, budget: FetchBudget, product_name: str | None = None) -> StorefrontResolution:
        """Resolve a single user-supplied listing URL (live listing import)."""
        return await self._resolve(url, product_name, budget, 0, [])

    async def _resolve(self, url: str, name: str | None, budget: FetchBudget, depth: int,
                       hops: list[str]) -> StorefrontResolution:
        fr = await self.fetcher.fetch(url, budget)
        hops = hops + [url] + [h for h in fr.hops if h != url]
        base = dict(requested_url=url, hops=hops, fetched_at=fr.fetched_at, http_status=fr.status,
                    is_fixture=fr.is_fixture)
        if not fr.ok:
            if fr.blocked_reason:
                return StorefrontResolution(status="BLOCKED", blocked_reason=fr.blocked_reason, error=fr.error,
                                            manual_only=True, store_url=fr.final_url,
                                            store_type=classify_url(fr.final_url).store_type, **base)
            return StorefrontResolution(status="ERROR", error=fr.error, manual_only=True, **base)

        final = fr.final_url
        cls = classify_url(final)
        if cls.kind in ("SOCIAL", "MEDIA"):
            return StorefrontResolution(status="UNSUPPORTED", manual_only=True,
                                        error="The link leads to a social or media page, not a store.", **base)

        page = parse_page(fr.text, final, cls.store_type)
        data = page.data

        if cls.kind == "LINK_HUB" or (cls.kind == "OTHER" and not page.is_product and depth == 0):
            # Expand one level: collect store links listed on the page.
            stores = []
            for href in page.outbound:
                c = classify_url(href)
                if c.kind == "STOREFRONT":
                    stores.append((0 if c.is_product_page else 1, href))
            stores.sort()
            best: StorefrontResolution | None = None
            if depth < self.MAX_DEPTH:
                for _prio, href in stores[:3]:
                    r = await self._resolve(href, name, budget, depth + 1, hops)
                    if r.status == "RESOLVED_PRODUCT":
                        return r
                    if best is None or _STATUS_RANK[r.status] > _STATUS_RANK[best.status]:
                        best = r
            if best is not None:
                return best
            kind = "Link page" if cls.kind == "LINK_HUB" else "Page"
            return StorefrontResolution(status="UNSUPPORTED", manual_only=True, store_url=final,
                                        injection_flags=data.injection_flags,
                                        error=f"{kind} lists no recognizable store or product link.", **base)

        priced_listing = cls.is_product_page and bool(data.product_title) and data.price_jpy is not None
        if page.is_product or priced_listing:
            data.status = "RESOLVED_PRODUCT"
            data.purchase_url = final
            data.store_url = f"{urlsplit(final).scheme}://{urlsplit(final).netloc}"
            data.store_name = data.store_name or STORE_LABELS.get(data.store_type or "", None) or urlsplit(final).hostname
            for k, v in base.items():
                setattr(data, k, v)
            if data.store_type in BIDDING_STORE_TYPES:
                data.manual_only = True
                data.restrictions.append("Auction listing: bidding is binding and is never drafted by KAI")
            return data

        if cls.kind == "STOREFRONT":
            # A shop front page: try to find the announced product among its own links.
            if name and depth < self.MAX_DEPTH:
                best_link, best_score = None, 0.0
                for href, text in page.same_host_links:
                    if not classify_url(href).is_product_page or not text:
                        continue
                    score = ngram_containment(name, text)
                    if score > best_score:
                        best_link, best_score = href, score
                if best_link and best_score >= 0.5:
                    r = await self._resolve(best_link, name, budget, depth + 1, hops)
                    if r.status == "RESOLVED_PRODUCT":
                        r.evidence.append(Evidence(field="product_match", source="store page link text",
                                                   snippet=f"Matched announced name to a store listing (overlap {best_score:.0%})"))
                        return r
            return StorefrontResolution(
                status="STORE_ONLY", manual_only=True, store_url=final, store_type=cls.store_type,
                store_name=data.store_name or STORE_LABELS.get(cls.store_type or "", None),
                injection_flags=data.injection_flags,
                error="Store found, but the announced product's page could not be identified.", **base)

        return StorefrontResolution(status="UNSUPPORTED", manual_only=True, store_url=final,
                                    injection_flags=data.injection_flags,
                                    error="Page is not a recognizable product listing.", **base)
