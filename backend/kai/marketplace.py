"""Marketplace listings to compare social drops against.

Sources, in order:
- Rakuten Ichiba Item Search API (official; needs RAKUTEN_APP_ID + RAKUTEN_ACCESS_KEY)
- Listings the user imported by URL (fetched live through SafeFetcher)
- Bundled fixture listings, labeled, only when real sources return too little
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .config import PKG, Settings
from .models import Availability, Candidate, new_id, now_iso
from .net import FetchBudget
from .observability import ActivityLog
from .security import detect_injection, sanitize_untrusted
from .store import Store
from .textutil import nfkc
from .vocab import CATEGORIES, detect_attributes
from .xdiscovery.storefront import STORE_LABELS, StorefrontTracer

RAKUTEN_ENDPOINT = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
MIN_REAL_CANDIDATES = 3


class MarketplaceService:
    def __init__(self, settings: Settings, store: Store, log: ActivityLog, tracer: StorefrontTracer,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.store = store
        self.log = log
        self.tracer = tracer
        self._transport = transport

    def rakuten_configured(self) -> bool:
        return bool(self.settings.rakuten_app_id and self.settings.rakuten_access_key)

    async def search(self, category: str, ja_terms: list[str], run_id: str | None = None
                     ) -> tuple[list[Candidate], list[dict[str, Any]]]:
        sources: list[dict[str, Any]] = []
        out: list[Candidate] = []

        # ---- Rakuten Ichiba ---------------------------------------------
        if self.rakuten_configured() and ja_terms:
            cands, err = await self._rakuten(ja_terms[0])
            sources.append({"provider": "RAKUTEN_ICHIBA", "state": "error" if err else ("ok" if cands else "empty"),
                            "count": len(cands), "error": err, "isFixture": False, "retrievedAt": now_iso()})
            self.log.log("marketplace", f"Rakuten Ichiba search for {ja_terms[0]}: "
                                        f"{'failed - ' + err if err else str(len(cands)) + ' listing(s)'}",
                         "error" if err else "ok", run_id)
            out.extend(cands)
        else:
            sources.append({"provider": "RAKUTEN_ICHIBA", "state": "unconfigured", "count": 0, "isFixture": False,
                            "error": "RAKUTEN_APP_ID / RAKUTEN_ACCESS_KEY not set; no live marketplace search was made."})

        # ---- user-imported listings -------------------------------------
        terms = [t.lower() for t in ja_terms]
        if category in CATEGORIES:
            terms += [t.lower() for t in CATEGORIES[category]["en"]]
        imported = []
        for d in self.store.list("imported_listing"):
            c = Candidate.model_validate(d)
            hay = nfkc(f"{c.title} {c.description or ''}").lower()
            if any(t in hay for t in terms):
                imported.append(c)
        sources.append({"provider": "URL_IMPORT", "state": "ok" if imported else "empty", "count": len(imported),
                        "isFixture": False, "error": None})
        out.extend(imported)

        # ---- fixtures ---------------------------------------------------
        if len(out) < MIN_REAL_CANDIDATES and self.settings.allow_fixtures:
            fx = self._fixtures(category)
            sources.append({"provider": "FIXTURE_MARKETPLACE", "state": "ok" if fx else "empty", "count": len(fx),
                            "isFixture": True, "error": None,
                            "note": "SAMPLE DATA: invented listings, shown because live marketplace sources returned too little."})
            if fx:
                self.log.log("marketplace", f"Added {len(fx)} FIXTURE marketplace listing(s) for comparison (sample data).",
                             "warn", run_id)
            out.extend(fx)
        return out, sources

    async def _rakuten(self, keyword: str) -> tuple[list[Candidate], str | None]:
        headers = {"accessKey": self.settings.rakuten_access_key or "", "Accept": "application/json"}
        if self.settings.rakuten_referer:
            headers["Referer"] = self.settings.rakuten_referer
            headers["Origin"] = self.settings.rakuten_referer.rstrip("/")
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(12.0, connect=5.0)) as client:
                r = await client.get(RAKUTEN_ENDPOINT, headers=headers, params={
                    "applicationId": self.settings.rakuten_app_id or "", "keyword": keyword, "hits": "10",
                    "format": "json", "formatVersion": "2", "availability": "1"})
        except httpx.HTTPError as e:
            return [], f"Network error: {type(e).__name__}"
        if r.status_code >= 400:
            return [], f"HTTP {r.status_code}: {r.text[:160]}"
        self.log.usage("rakuten", "ichiba_item_search", 1, "queries", 0.0, {"keyword": keyword})
        body = r.json()
        items = body.get("Items") or body.get("items") or []
        out: list[Candidate] = []
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("Item"), dict):
                it = it["Item"]
            title = sanitize_untrusted(str(it.get("itemName") or ""), 200)
            if not title or not it.get("itemUrl"):
                continue
            desc = sanitize_untrusted(str(it.get("itemCaption") or ""), 400)
            images = it.get("mediumImageUrls") or []
            img = images[0] if images and isinstance(images[0], str) else (
                images[0].get("imageUrl") if images and isinstance(images[0], dict) else None)
            out.append(Candidate(
                id=f"rk_{it.get('itemCode') or new_id('i')}", source="MARKETPLACE", source_label="Rakuten Ichiba (official API)",
                title=title, description=desc or None,
                price_jpy=int(it["itemPrice"]) if str(it.get("itemPrice") or "").isdigit() else None,
                purchase_url=it["itemUrl"], store_type="rakuten", store_name=it.get("shopName"),
                availability=Availability.IN_STOCK if it.get("availability") == 1 else Availability.UNKNOWN,
                attributes=detect_attributes(f"{title} {desc}"), image_url=img, verification="MARKETPLACE_API",
                retrieved_at=now_iso(), injection_flags=detect_injection(f"{title} {desc}"),
            ))
        return out, None

    def _fixtures(self, category: str) -> list[Candidate]:
        data = json.loads((PKG / "fixtures" / "marketplace.json").read_text(encoding="utf-8"))
        out = []
        for it in data["listings"]:
            if it["category"] != category:
                continue
            out.append(Candidate(
                id=f"fx_{it['id']}", source="MARKETPLACE", source_label="Sample marketplace (FIXTURE)",
                title=it["title"], description=it.get("description"), price_jpy=it.get("priceJpy"),
                purchase_url=it["url"], store_type="independent", store_name=it.get("storeName"),
                availability=Availability(it.get("availability", "UNKNOWN")),
                attributes=detect_attributes(f"{it['title']} {it.get('description') or ''}"), is_fixture=True,
                verification="FIXTURE", retrieved_at=now_iso(),
            ))
        return out

    async def import_url(self, url: str, run_id: str | None = None) -> tuple[Candidate | None, str | None]:
        """Live listing import: fetch a product page the user supplies and parse it."""
        res = await self.tracer.resolve_url(url, FetchBudget(4))
        if res.status != "RESOLVED_PRODUCT":
            msg = res.error or res.blocked_reason or f"Could not read a product listing ({res.status})."
            self.log.log("listing_import", f"Listing import failed for {url[:120]}: {msg}", "warn", run_id,
                         {"status": res.status})
            return None, msg
        cand = Candidate(
            id=new_id("imp"), source="URL_IMPORT",
            source_label=f"Imported listing ({STORE_LABELS.get(res.store_type or '', res.store_type or 'store')})"
                         + (" (FIXTURE page)" if res.is_fixture else ""),
            title=res.product_title or "Untitled listing", description=res.description_excerpt,
            price_jpy=res.price_jpy, purchase_url=res.purchase_url, store_type=res.store_type,
            store_name=res.store_name, availability=res.availability, attributes=res.attributes,
            image_url=res.image_urls[0] if res.image_urls else None, is_fixture=res.is_fixture,
            verification="FIXTURE" if res.is_fixture else "LISTING_RESOLVED",
            ships_internationally=res.ships_internationally, restrictions=res.restrictions,
            retrieved_at=res.fetched_at, injection_flags=res.injection_flags,
        )
        self.store.put("imported_listing", cand.id, cand.to_json())
        self.log.log("listing_import", f"Imported listing: {cand.title} - "
                                       f"{('¥%s' % format(cand.price_jpy, ',')) if cand.price_jpy is not None else 'price unknown'}, "
                                       f"{cand.availability}", "ok", run_id, {"url": cand.purchase_url})
        return cand, None
