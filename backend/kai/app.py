"""Application wiring and HTTP API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import PKG, Settings
from .errors import LiveDataUnavailable
from .hunts import HuntService
from .cost import FxService, LandedCostEstimator
from .integrations.cognee_memory import CogneeAdapter
from .integrations.senso import SensoAdapter
from .integrations.sie import SIEAdapter
from .llm import LLMClient
from .marketplace import MarketplaceService
from .memory import MemoryService
from .models import (AgentRun, Candidate, ParsedRequest, RankedCandidate, ScoreBreakdown, SocialProductLead,
                     WatchedCreator, new_id, now_iso)
from .net import Resolver, SafeFetcher
from .observability import ActivityLog
from .orchestrator import DEFAULT_DESTINATION, ShoppingAgent
from .purchase import PurchaseService
from .ranking import Ranker, purchase_path
from .store import Store
from .vocab import ATTRIBUTES, CATEGORIES
from .xdiscovery.agent import XDiscoveryAgent
from .xdiscovery.extraction import LeadExtractor
from .xdiscovery.providers import (FixtureProvider, IndexedWebSearchProvider, ManualPostImportProvider,
                                   XOfficialAPIProvider)
from .xdiscovery.query_engine import _HANDLE
from .xdiscovery.storefront import StorefrontTracer


@dataclass
class Kai:
    settings: Settings
    store: Store
    log: ActivityLog
    fetcher: SafeFetcher
    llm: LLMClient
    sie: SIEAdapter
    senso: SensoAdapter
    cognee: CogneeAdapter
    memory: MemoryService
    official: XOfficialAPIProvider
    discovery: XDiscoveryAgent
    marketplace: MarketplaceService
    costs: LandedCostEstimator
    purchase: PurchaseService
    agent: ShoppingAgent
    hunts: HuntService


def build(settings: Settings, transport: httpx.AsyncBaseTransport | None = None, resolver: Resolver | None = None,
          cognee_module: Any | None = None, db_path: str | None = None) -> Kai:
    """Wire the application. `transport` replaces every outbound HTTP call (tests)."""
    # Sample data needs KAI_LIVE_ONLY=false and KAI_ALLOW_FIXTURES=true; it is served from disk and labeled.
    store = Store(db_path or settings.db_path)
    if not settings.allow_fixtures:
        store.hide_sample_documents()
    log = ActivityLog(store)
    fetcher = SafeFetcher(settings, store, log, transport, resolver)
    llm = LLMClient(settings, log, transport)
    sie = SIEAdapter(settings, log, transport)
    senso = SensoAdapter(settings, store, log, transport)
    cognee = CogneeAdapter(settings, log, cognee_module)
    memory = MemoryService(store, log, cognee)
    official = XOfficialAPIProvider(settings, store, log, transport)
    indexed = IndexedWebSearchProvider(settings, store, log, transport)
    manual = ManualPostImportProvider(store, log, official)
    fixture = FixtureProvider(settings)
    tracer = StorefrontTracer(fetcher, log)
    discovery = XDiscoveryAgent(settings, store, log, official, indexed, manual, fixture, LeadExtractor(llm), tracer)
    marketplace = MarketplaceService(settings, store, log, tracer, transport)
    costs = LandedCostEstimator(settings, senso, FxService(settings, store, log, transport), log)
    purchase = PurchaseService(store, log)
    agent = ShoppingAgent(store, log, llm, memory, discovery, marketplace, costs, Ranker(sie, log), purchase, sie,
                          senso, settings.fetch_max_per_run)
    return Kai(settings, store, log, fetcher, llm, sie, senso, cognee, memory, official, discovery, marketplace,
               costs, purchase, agent, HuntService(store, agent))


# ---- request bodies -------------------------------------------------------


class RunBody(BaseModel):
    message: str = Field(min_length=2, max_length=1000)


class HuntBody(BaseModel):
    runId: str
    title: str | None = Field(default=None, max_length=100)


class RefreshBody(BaseModel):
    category: str | None = None


class ImportPostBody(BaseModel):
    url: str
    text: str | None = None
    authorHandle: str | None = None
    links: list[str] = Field(default_factory=list)
    postedAt: str | None = None


class WatchedBody(BaseModel):
    handle: str | None = None
    categories: list[str] = Field(default_factory=list)
    searchTerms: list[str] = Field(default_factory=list)
    note: str | None = None


class PreferenceBody(BaseModel):
    text: str = Field(min_length=2, max_length=400)


class ImportListingBody(BaseModel):
    url: str


class ShortlistBody(BaseModel):
    leadId: str | None = None
    runId: str | None = None
    candidateId: str | None = None


class DraftBody(BaseModel):
    runId: str | None = None
    candidateId: str | None = None
    shortlistId: str | None = None


class ReviewBody(BaseModel):
    decision: str
    note: str | None = None


def create_app(settings: Settings | None = None, kai: Kai | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    k = kai or build(settings or Settings.from_env())
    app = FastAPI(title="KAI", version="0.1.0")
    app.state.kai = k

    @app.exception_handler(LiveDataUnavailable)
    async def live_unavailable(_request, error: LiveDataUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(error), "service": error.service,
                                                     "liveOnly": True})
    web = PKG / "web"
    app.mount("/static", StaticFiles(directory=str(web)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(str(web / "index.html"))

    # ---- status ----------------------------------------------------------
    def status_payload() -> dict[str, Any]:
        return {
            "integrations": k.agent.integrations(),
            "xProviders": k.discovery.provider_overview(),
            "marketplace": {"rakutenConfigured": k.marketplace.rakuten_configured()},
            "fixturesAllowed": k.settings.allow_fixtures,
            "liveOnly": k.settings.live_only,
            "usage": k.store.usage_summary(),
            "xSpend": {"spentUsd": round(k.official.spent(), 4), "maxUsd": k.settings.x_max_spend_usd,
                       "note": "Estimated from returned post/user counts and configured unit prices."},
            "categories": [{"key": key, "label": v["label"], "ja": v["ja"]} for key, v in CATEGORIES.items()],
            "attributes": [{"key": key, "label": v["label"]} for key, v in ATTRIBUTES.items()],
            "bindingActionsSupported": [],
        }

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return status_payload()

    @app.post("/api/integrations/check")
    async def check_integrations() -> dict[str, Any]:
        """Bounded live connectivity checks for the integrations that are configured."""
        await k.sie.health()
        await k.senso.whoami()
        k.log.log("integrations", "Connectivity check: " + "; ".join(
            f"{i['label']}: {i['mode']}" for i in k.agent.integrations()), "info")
        return status_payload()

    @app.post("/api/integrations/senso/seed")
    async def senso_seed() -> dict[str, Any]:
        return await k.senso.seed()

    # ---- agent -----------------------------------------------------------
    @app.post("/api/agent/run")
    async def agent_run(body: RunBody) -> dict[str, Any]:
        result = await k.agent.run(body.message)
        return result.to_json() if isinstance(result, AgentRun) else result

    @app.get("/api/runs")
    async def runs() -> list[dict[str, Any]]:
        return [{"id": r["id"], "createdAt": r["createdAt"], "raw": r["request"]["raw"], "summary": r["summary"]}
                for r in k.store.list("run", 30)]

    @app.get("/api/runs/{run_id}")
    async def run_get(run_id: str) -> dict[str, Any]:
        r = k.store.get("run", run_id)
        if not r:
            raise HTTPException(404, "Run not found")
        return r

    # ---- saved hunts -----------------------------------------------------
    @app.get("/api/hunts")
    async def hunts_list() -> list[dict[str, Any]]:
        return k.hunts.list()

    @app.post("/api/hunts")
    async def hunt_create(body: HuntBody) -> dict[str, Any]:
        try:
            return k.hunts.create(body.runId, body.title)
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    @app.post("/api/hunts/{hunt_id}/check")
    async def hunt_check(hunt_id: str) -> dict[str, Any]:
        try:
            return await k.hunts.check(hunt_id)
        except KeyError:
            raise HTTPException(404, "Hunt not found") from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @app.delete("/api/hunts/{hunt_id}")
    async def hunt_delete(hunt_id: str) -> dict[str, Any]:
        try:
            return {"deleted": k.hunts.delete(hunt_id)}
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    # ---- social drops ----------------------------------------------------
    @app.get("/api/social/leads")
    async def leads(category: str | None = None) -> dict[str, Any]:
        items = k.store.list("lead", 300)
        if category:
            items = [l for l in items if l.get("productCategory") == category]
        items.sort(key=lambda l: l.get("sourceTimestamp") or "", reverse=True)
        return {"leads": items, "providers": k.discovery.provider_overview(),
                "lastRefresh": (k.store.get("meta", "last_refresh") or {})}

    @app.post("/api/social/refresh")
    async def social_refresh(body: RefreshBody) -> dict[str, Any]:
        """On-demand refresh. There is no background polling."""
        if body.category:
            cats = [body.category]
        else:
            cats = []
            for w in k.store.list("watched"):
                for c in w.get("categories") or []:
                    if c in CATEGORIES and c not in cats:
                        cats.append(c)
            for s in k.memory.past_searches():
                if s["category"] in CATEGORIES and s["category"] not in cats:
                    cats.append(s["category"])
            cats = cats[:2] or ["keycaps"]
        if any(c not in CATEGORIES for c in cats):
            raise HTTPException(400, f"Unknown category. Known: {', '.join(CATEGORIES)}")
        run_id = new_id("refresh")
        mem = await k.memory.recall("social drops refresh", run_id=run_id)
        results = []
        for c in cats:
            res = await k.discovery.discover(c, mem.preferred, k.memory.past_searches(), run_id=run_id)
            results.append(res.to_json())
        meta = {"at": now_iso(), "runId": run_id, "categories": cats,
                "fresh": any(r["freshDiscovery"] for r in results),
                "providerStatuses": [s for r in results for s in r["providerStatuses"]],
                "notes": [n for r in results for n in r["notes"]],
                "queries": [q for r in results for q in r["queries"]],
                "stats": [dict(r["stats"], category=r["category"]) for r in results]}
        k.store.put("meta", "last_refresh", meta)
        return {"results": results, "lastRefresh": meta}

    @app.post("/api/social/import")
    async def social_import(body: ImportPostBody) -> dict[str, Any]:
        lead, err = await k.discovery.import_post(body.url, body.text, body.authorHandle, body.links, body.postedAt)
        if lead is None:
            raise HTTPException(400, err or "Import failed")
        return lead.to_json()

    @app.delete("/api/social/leads/{lead_id}")
    async def lead_delete(lead_id: str) -> dict[str, Any]:
        return {"deleted": k.store.delete("lead", lead_id)}

    # ---- watched creators ------------------------------------------------
    @app.get("/api/watched")
    async def watched_list() -> list[dict[str, Any]]:
        return k.store.list("watched")

    @app.post("/api/watched")
    async def watched_add(body: WatchedBody) -> dict[str, Any]:
        handle = (body.handle or "").strip().lstrip("@") or None
        if handle and not _HANDLE.match(handle):
            raise HTTPException(400, "X handles are 1-15 letters, digits or underscores.")
        cats = [c for c in body.categories if c in CATEGORIES]
        terms = [t.strip()[:40] for t in body.searchTerms if t.strip()][:5]
        if not handle and not terms:
            raise HTTPException(400, "Provide a public X handle or at least one search term.")
        w = WatchedCreator(id=new_id("watch"), handle=handle, categories=cats, search_terms=terms,
                           note=(body.note or "").strip()[:200] or None)
        k.store.put("watched", w.id, w.to_json())
        k.log.log("watched", f"Watching {('@' + handle) if handle else 'search terms'}"
                             f"{' · ' + ', '.join(terms) if terms else ''}", "ok")
        return w.to_json()

    @app.delete("/api/watched/{watch_id}")
    async def watched_delete(watch_id: str) -> dict[str, Any]:
        return {"deleted": k.store.delete("watched", watch_id)}

    # ---- shortlist -------------------------------------------------------
    def _ranked_from_run(run_id: str, candidate_id: str) -> tuple[RankedCandidate, ParsedRequest]:
        r = k.store.get("run", run_id)
        if not r:
            raise HTTPException(404, "Run not found")
        for rc in r.get("ranked", []):
            if rc["candidate"]["id"] == candidate_id:
                return RankedCandidate.model_validate(rc), ParsedRequest.model_validate(r["request"])
        raise HTTPException(404, "Candidate not found in that run")

    @app.get("/api/shortlist")
    async def shortlist_list() -> list[dict[str, Any]]:
        return k.store.list("shortlist")

    @app.post("/api/shortlist")
    async def shortlist_add(body: ShortlistBody) -> dict[str, Any]:
        if body.leadId:
            d = k.store.get("lead", body.leadId)
            if not d:
                raise HTTPException(404, "Lead not found")
            lead = SocialProductLead.model_validate(d)
            cand = ShoppingAgent.lead_to_candidate(lead)
            lead.shortlisted = True
            k.store.put("lead", lead.id, lead.to_json())
        elif body.runId and body.candidateId:
            rc, _req = _ranked_from_run(body.runId, body.candidateId)
            cand = rc.candidate
        else:
            raise HTTPException(400, "Provide leadId, or runId and candidateId.")
        existing = next((s for s in k.store.list("shortlist") if s["candidate"]["id"] == cand.id), None)
        if existing:
            return existing
        item = {"id": new_id("short"), "addedAt": now_iso(), "candidate": cand.to_json(), "leadId": cand.lead_id,
                "runId": body.runId}
        k.store.put("shortlist", item["id"], item)
        k.log.log("shortlist", f"Added to shortlist: {cand.title[:60]}", "ok")
        return item

    @app.delete("/api/shortlist/{item_id}")
    async def shortlist_delete(item_id: str) -> dict[str, Any]:
        item = k.store.get("shortlist", item_id)
        if item and item.get("leadId"):
            d = k.store.get("lead", item["leadId"])
            if d:
                d["shortlisted"] = False
                k.store.put("lead", d["id"], d)
        return {"deleted": k.store.delete("shortlist", item_id)}

    # ---- drafts + review gate -------------------------------------------
    @app.get("/api/drafts")
    async def drafts() -> list[dict[str, Any]]:
        return [d.to_json() for d in k.purchase.list()]

    @app.post("/api/drafts")
    async def draft_create(body: DraftBody) -> dict[str, Any]:
        """Prepare a nonbinding draft for a ranked candidate or a shortlisted item."""
        lead = None
        if body.runId and body.candidateId:
            rc, req = _ranked_from_run(body.runId, body.candidateId)
        elif body.shortlistId:
            item = k.store.get("shortlist", body.shortlistId)
            if not item:
                raise HTTPException(404, "Shortlist item not found")
            cand = Candidate.model_validate(item["candidate"])
            req = ParsedRequest(raw="(from shortlist)", category=(k.store.get("lead", cand.lead_id or "") or {}).get(
                "productCategory") or "default", category_label="Shortlist", ja_terms=[], destination=DEFAULT_DESTINATION)
            observed = cand.verification in ("LISTING_RESOLVED", "MARKETPLACE_API", "FIXTURE")
            cost = await k.costs.estimate(cand, req.category, None, req.destination, observed)
            claimed = False
            if cand.lead_id and (d := k.store.get("lead", cand.lead_id)):
                claimed = d.get("announcementType") == "SOLD_OUT" and d.get("availabilityStatus") == "UNKNOWN"
            path, why = purchase_path(cand, claimed)
            rc = RankedCandidate(candidate=cand, cost=cost, purchase_path=path, purchase_path_reason=why,
                                 score=ScoreBreakdown(ranking_backend="not ranked (shortlist)"))
        else:
            raise HTTPException(400, "Provide runId and candidateId, or shortlistId.")
        if rc.candidate.lead_id and (d := k.store.get("lead", rc.candidate.lead_id)):
            lead = SocialProductLead.model_validate(d)
        if rc.purchase_path == "WATCH_ONLY":
            raise HTTPException(409, f"No draft possible: {rc.purchase_path_reason}")
        return k.purchase.create_draft(rc, req, lead, body.runId).to_json()

    @app.post("/api/drafts/{draft_id}/review")
    async def draft_review(draft_id: str, body: ReviewBody) -> dict[str, Any]:
        try:
            return k.purchase.review(draft_id, body.decision, body.note).to_json()
        except KeyError:
            raise HTTPException(404, "Draft not found") from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    # ---- memory ----------------------------------------------------------
    @app.get("/api/memory")
    async def memory_get() -> dict[str, Any]:
        return {"preferences": k.memory.list_preferences(), "searches": k.memory.past_searches(),
                "backend": k.cognee.state.to_json()}

    @app.post("/api/memory/preferences")
    async def memory_add(body: PreferenceBody) -> dict[str, Any]:
        saved = await k.memory.add_preference(body.text)
        return {"saved": saved, "backend": k.cognee.state.to_json()}

    @app.delete("/api/memory/preferences/{pref_id}")
    async def memory_delete(pref_id: str) -> dict[str, Any]:
        return {"deleted": k.memory.delete_preference(pref_id)}

    # ---- live listing import --------------------------------------------
    @app.get("/api/listings")
    async def listings() -> list[dict[str, Any]]:
        return k.store.list("imported_listing")

    @app.post("/api/listings/import")
    async def listing_import(body: ImportListingBody) -> dict[str, Any]:
        cand, err = await k.marketplace.import_url(body.url.strip())
        if cand is None:
            raise HTTPException(422, err or "Could not import that listing")
        return cand.to_json()

    @app.delete("/api/listings/{listing_id}")
    async def listing_delete(listing_id: str) -> dict[str, Any]:
        return {"deleted": k.store.delete("imported_listing", listing_id)}

    # ---- observability ---------------------------------------------------
    @app.get("/api/activity")
    async def activity(runId: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        return k.store.events(runId, min(limit, 500))

    return app
