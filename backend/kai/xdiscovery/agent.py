"""XDiscoveryAgent: queries -> provider selection -> extraction -> dedupe ->
storefront tracing -> persisted leads, with every step logged."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from ..config import Settings
from ..models import (Availability, DiscoveryResult, DiscoveryStats, ProviderName, ProviderStatus,
                      SocialProductLead, Verification, now_iso)
from ..net import FetchBudget
from ..observability import ActivityLog
from ..store import Store
from ..vocab import CATEGORIES
from .dedupe import dedupe_leads
from .extraction import LeadExtractor
from .providers import (FixtureProvider, IndexedWebSearchProvider, ManualPostImportProvider, RawPost,
                        XOfficialAPIProvider)
from .query_engine import build_queries
from .storefront import StorefrontTracer

MIN_LEADS_TARGET = 3
PURCHASABLE = {Availability.IN_STOCK.value, Availability.PREORDER.value, Availability.MADE_TO_ORDER.value}

_VERIFICATION = {
    "RESOLVED_PRODUCT": Verification.LISTING_RESOLVED,
    "STORE_ONLY": Verification.STORE_FOUND_PRODUCT_UNRESOLVED,
    "BLOCKED": Verification.BLOCKED,
    "UNSUPPORTED": Verification.UNSUPPORTED_STORE,
    "ERROR": Verification.STORE_LINK_UNRESOLVED,
    "NO_LINK": Verification.ANNOUNCEMENT_ONLY,
}


def lead_id_for(post: RawPost) -> str:
    key = post.post_id or hashlib.sha1(post.url.encode()).hexdigest()[:16]
    prefix = "fx" if post.is_fixture else "x"
    return f"lead_{prefix}_{key}"


class XDiscoveryAgent:
    def __init__(self, settings: Settings, store: Store, log: ActivityLog, official: XOfficialAPIProvider,
                 indexed: IndexedWebSearchProvider, manual: ManualPostImportProvider, fixture: FixtureProvider,
                 extractor: LeadExtractor, tracer: StorefrontTracer):
        self.settings = settings
        self.store = store
        self.log = log
        self.official = official
        self.indexed = indexed
        self.manual = manual
        self.fixture = fixture
        self.extractor = extractor
        self.tracer = tracer

    # ---- public ----------------------------------------------------------
    def provider_overview(self) -> list[dict[str, Any]]:
        """What each provider could do right now (no network calls)."""
        s = self.settings
        return [
            {"provider": ProviderName.X_OFFICIAL_API.value, "configured": self.official.configured(),
             "coverage": self.official.coverage,
             "detail": (f"Bearer token set. Estimated spend ${self.official.spent():.3f} of ${s.x_max_spend_usd:.2f} ceiling."
                        if self.official.configured() else "X_BEARER_TOKEN not set."),
             "spentUsd": round(self.official.spent(), 4), "maxSpendUsd": s.x_max_spend_usd},
            {"provider": ProviderName.INDEXED_WEB_SEARCH.value, "configured": self.indexed.configured(),
             "coverage": self.indexed.coverage,
             "detail": "Brave Search API key set." if self.indexed.configured() else "BRAVE_SEARCH_API_KEY not set."},
            {"provider": ProviderName.MANUAL_IMPORT.value, "configured": True, "coverage": self.manual.coverage,
             "detail": f"{len(self.store.list('manual_post'))} imported post(s) stored."},
            {"provider": ProviderName.FIXTURE.value, "configured": self.fixture.configured(),
             "coverage": self.fixture.coverage,
             "detail": "Used only when live providers return too little." if self.fixture.configured() else "Disabled."},
        ]

    async def discover(self, category: str, preferred_attributes: list[str] | None = None,
                       history: list[dict[str, Any]] | None = None, extra_terms: list[str] | None = None,
                       run_id: str | None = None, budget: FetchBudget | None = None,
                       ja_terms: list[str] | None = None) -> DiscoveryResult:
        label = CATEGORIES.get(category, {}).get("label") or (ja_terms[0] if ja_terms else category)
        watched = self.store.list("watched")
        queries = build_queries(category, preferred_attributes, history, watched, extra_terms, ja_terms=ja_terms)
        result = DiscoveryResult(category=category, queries=queries)
        valid = [q for q in queries if q.valid]
        self.log.log("x_queries", f"Generated {len(queries)} Japanese search queries for {label} "
                                  f"({len(valid)} valid)", "ok" if valid else "error", run_id,
                     {"queries": [{"text": q.text, "intent": q.intent, "source": q.source, "valid": q.valid,
                                   "errors": q.errors} for q in queries]})

        # ---- provider selection -----------------------------------------
        posts: list[RawPost] = []
        live_counts: dict[ProviderName, int] = {}
        for provider in (self.official, self.indexed):
            got, st = await provider.search(valid, run_id)
            result.provider_statuses.append(st)
            live_counts[provider.name] = len(got)
            if st.state == "unconfigured":
                self.log.log("x_provider", f"{provider.name.value}: not configured. {st.error}", "info", run_id)
            elif st.state not in ("ok", "empty"):
                self.log.log("x_provider", f"{provider.name.value} failed ({st.state}): {st.error}", "error", run_id,
                             {"provider": provider.name.value, "state": st.state, "httpStatus": st.http_status})
            else:
                self.log.log("x_provider", f"{provider.name.value}: {len(got)} post(s) from {st.queries_run} query(ies)"
                                           f"{' (served from cache)' if st.from_cache else ''}",
                             "ok" if got else "warn", run_id, {"provider": provider.name.value, "posts": len(got)})
            posts.extend(got)
            if len(posts) >= MIN_LEADS_TARGET:
                break
        for name in (ProviderName.X_OFFICIAL_API, ProviderName.INDEXED_WEB_SEARCH):
            if all(s.provider != name.value for s in result.provider_statuses):
                result.provider_statuses.append(ProviderStatus(
                    provider=name, state="skipped", error="Not needed: an earlier provider returned enough posts."))
        live_total = len(posts)

        manual_posts, manual_st = await self.manual.search(valid, run_id, category=category)
        result.provider_statuses.append(manual_st)
        posts.extend(manual_posts)

        fixture_used = False
        if len(posts) < MIN_LEADS_TARGET:
            fx_posts, fx_st = await self.fixture.search(valid, run_id, category=category)
            result.provider_statuses.append(fx_st)
            if fx_posts:
                fixture_used = True
                posts.extend(fx_posts)
                self.log.log("x_provider", f"Live X discovery returned {live_total} post(s); added {len(fx_posts)} "
                                           "bundled FIXTURE post(s). These are sample data, not real X posts.",
                             "warn", run_id, {"provider": "FIXTURE", "posts": len(fx_posts)})
        else:
            result.provider_statuses.append(ProviderStatus(
                provider=ProviderName.FIXTURE, state="skipped", is_fixture=True,
                error="Not used: live or imported posts were available."))

        result.fresh_discovery = live_total > 0
        best_live = max(live_counts.items(), key=lambda kv: kv[1], default=(None, 0))
        if best_live[1] > 0:
            result.selected_provider = best_live[0]
        elif fixture_used:
            result.selected_provider = ProviderName.FIXTURE
        elif manual_posts:
            result.selected_provider = ProviderName.MANUAL_IMPORT
        for st in result.provider_statuses:
            st.selected = result.selected_provider is not None and st.provider == ProviderName(result.selected_provider).value
        if not result.fresh_discovery:
            result.notes.append(
                "No fresh X discovery in this pass: no live provider returned posts. "
                + ("Showing bundled fixtures (sample data) and any posts you imported." if fixture_used or manual_posts
                   else "Nothing to show; add credentials or import a post."))

        stats = DiscoveryStats(posts_retrieved=len(posts))

        # ---- extraction (bounded concurrency) ---------------------------
        sem = asyncio.Semaphore(3)

        async def extract(post: RawPost) -> SocialProductLead | None:
            async with sem:
                try:
                    lead = await self.extractor.extract(post, category, run_id)
                    lead.id = lead_id_for(post)
                    return lead
                except Exception as e:  # noqa: BLE001
                    stats.extraction_failures += 1
                    self.log.log("x_extract", f"Extraction failed for {post.url}: {type(e).__name__}: {e}", "error", run_id)
                    return None

        leads = [l for l in await asyncio.gather(*(extract(p) for p in posts)) if l is not None]
        stats.translated = sum(1 for l in leads if l.english_translation)
        useful = [l for l in leads if l.useful]
        stats.not_useful = len(leads) - len(useful)
        methods = sorted({l.provenance.translation_method for l in leads})
        self.log.log("x_extract", f"Extracted {len(leads)} post(s): {len(useful)} look like product announcements, "
                                  f"{stats.not_useful} set aside. Translation: {', '.join(methods) or 'n/a'}.",
                     "ok", run_id, {"notUseful": [{"url": l.source_post_url, "reason": l.usefulness_reason}
                                                   for l in leads if not l.useful]})

        useful, removed1 = dedupe_leads(useful)

        # ---- storefront tracing -----------------------------------------
        budget = budget or FetchBudget(self.settings.fetch_max_per_run)
        useful.sort(key=lambda l: l.source_timestamp or "", reverse=True)  # recency first, not popularity

        async def trace(lead: SocialProductLead) -> None:
            async with sem:
                try:
                    res = await self.tracer.trace(lead.external_links, lead.product_name, budget, run_id)
                except Exception as e:  # noqa: BLE001
                    self.log.log("storefront", f"Storefront trace crashed for {lead.source_post_url}: {type(e).__name__}: {e}",
                                 "error", run_id)
                    return
                lead.storefront = res
                lead.verification_status = _VERIFICATION.get(res.status, Verification.ANNOUNCEMENT_ONLY)
                if res.status == "RESOLVED_PRODUCT":
                    lead.availability_status = res.availability
                    lead.attributes = sorted(set(lead.attributes) | set(res.attributes))
                    lead.injection_flags = sorted(set(lead.injection_flags) | set(res.injection_flags))
                status = "ok" if res.status == "RESOLVED_PRODUCT" else ("info" if res.status == "NO_LINK" else "warn")
                fx = " [fixture page]" if res.is_fixture else ""
                if res.status == "RESOLVED_PRODUCT":
                    msg = (f"Resolved store listing{fx}: {res.product_title or 'untitled'} at {res.store_name or res.store_type} "
                           f"- {('¥%s' % format(res.price_jpy, ',')) if res.price_jpy is not None else 'price unknown'}, "
                           f"{res.availability}")
                else:
                    msg = f"Storefront not resolved{fx} ({res.status}): {res.error or res.blocked_reason or ''}"
                if res.injection_flags:
                    msg += f" | instruction-like text on page flagged and ignored: {', '.join(res.injection_flags)}"
                    status = "warn"
                self.log.log("storefront", msg, status, run_id,
                             {"post": lead.source_post_url, "purchaseUrl": res.purchase_url, "hops": res.hops,
                              "status": res.status, "isFixture": res.is_fixture})

        await asyncio.gather(*(trace(l) for l in useful))
        useful, removed2 = dedupe_leads(useful)
        stats.duplicates_filtered = removed1 + removed2
        if stats.duplicates_filtered:
            self.log.log("x_dedupe", f"Filtered {stats.duplicates_filtered} duplicate post(s) pointing at the same product.",
                         "ok", run_id)

        stats.useful_announcements = len(useful)
        stats.storefront_links_resolved = sum(1 for l in useful if l.storefront and l.storefront.status == "RESOLVED_PRODUCT")
        stats.verified_purchasable = sum(
            1 for l in useful if l.storefront and l.storefront.status == "RESOLVED_PRODUCT"
            and l.storefront.availability in PURCHASABLE)
        stats.blocked_or_unsupported = sum(
            1 for l in useful if l.storefront and l.storefront.status in ("BLOCKED", "UNSUPPORTED", "ERROR", "STORE_ONLY"))
        result.stats = stats

        if len(useful) < MIN_LEADS_TARGET:
            result.notes.append(f"Found {len(useful)} relevant announcement lead(s); fewer than the {MIN_LEADS_TARGET} targeted.")
        if budget.used >= budget.max_requests:
            result.notes.append("The per-run storefront request budget was used up; some links were not followed.")

        # ---- persist (keep the user's shortlist flag) --------------------
        useful.sort(key=lambda l: l.source_timestamp or "", reverse=True)
        if not fixture_used:
            # Real posts were available, so stale sample leads for this category leave the feed.
            for d in self.store.list("lead", 500):
                if ((d.get("provenance") or {}).get("isFixture") and d.get("productCategory") == category
                        and not d.get("shortlisted")):
                    self.store.delete("lead", d["id"])
        for lead in useful:
            prev = self.store.get("lead", lead.id)
            if prev and prev.get("shortlisted"):
                lead.shortlisted = True
            self.store.put("lead", lead.id, lead.to_json())
        result.leads = useful
        result.retrieved_at = now_iso()
        fixture_n = sum(1 for l in useful if l.provenance.is_fixture)
        self.log.log("x_discovery", f"Discovery summary: {stats.posts_retrieved} post(s) retrieved, "
                                    f"{stats.useful_announcements} useful announcement(s)"
                                    f"{f' ({fixture_n} from fixtures)' if fixture_n else ''}, "
                                    f"{stats.storefront_links_resolved} store listing(s) resolved, "
                                    f"{stats.verified_purchasable} purchasable now, "
                                    f"{stats.blocked_or_unsupported} blocked/unsupported, "
                                    f"{stats.duplicates_filtered} duplicate(s) filtered.",
                     "ok" if useful else "warn", run_id, stats.to_json())
        return result

    async def import_post(self, url: str, text: str | None = None, author_handle: str | None = None,
                          links: list[str] | None = None, posted_at: str | None = None,
                          run_id: str | None = None) -> tuple[SocialProductLead | None, str | None]:
        post, err = await self.manual.import_post(url, text, author_handle, links, posted_at, run_id)
        if post is None:
            return None, err
        lead = await self.extractor.extract(post, None, run_id)
        lead.id = lead_id_for(post)
        lead.useful = True  # the user chose to import it
        lead.usefulness_reason = None
        res = await self.tracer.trace(lead.external_links, lead.product_name,
                                      FetchBudget(self.settings.fetch_max_per_run), run_id)
        lead.storefront = res
        lead.verification_status = _VERIFICATION.get(res.status, Verification.ANNOUNCEMENT_ONLY)
        if res.status == "RESOLVED_PRODUCT":
            lead.availability_status = res.availability
            lead.attributes = sorted(set(lead.attributes) | set(res.attributes))
            lead.injection_flags = sorted(set(lead.injection_flags) | set(res.injection_flags))
        self.log.log("storefront", f"Imported post storefront trace: {res.status}"
                                   f"{' - ' + (res.product_title or '') if res.status == 'RESOLVED_PRODUCT' else ''}"
                                   f"{' - ' + res.error if res.error else ''}",
                     "ok" if res.status == "RESOLVED_PRODUCT" else "warn", run_id)
        self.store.put("lead", lead.id, lead.to_json())
        return lead, None
