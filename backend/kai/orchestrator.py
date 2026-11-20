"""End-to-end shopping run:

request -> memory -> X discovery + marketplace -> ranking -> cited landed
cost -> nonbinding draft -> STOP for human review.

The orchestrator never takes a binding action. Its last step is always to
hand a draft (or manual next steps) to the human."""

from __future__ import annotations

import re
from typing import Any

from .cost import LandedCostEstimator
from .integrations.sie import SIEAdapter
from .integrations.senso import SensoAdapter
from .llm import LLMClient
from .marketplace import MarketplaceService
from .memory import MemoryService
from .models import (AgentRun, Availability, Candidate, ParsedRequest, RankedCandidate, SocialProductLead,
                     TimelineEvent, new_id)
from .net import FetchBudget
from .observability import ActivityLog
from .purchase import PurchaseService
from .ranking import Ranker
from .security import UNTRUSTED_SYSTEM_RULE, sanitize_untrusted, wrap_untrusted
from .store import Store
from .textutil import nfkc
from .vocab import ATTRIBUTES, CATEGORIES, detect_category
from .xdiscovery.agent import XDiscoveryAgent
from .xdiscovery.storefront import STORE_LABELS

_BUDGET = re.compile(r"(?:\$|usd\s*)\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*(?:usd|dollars|bucks)", re.I)
_JA_WORD = re.compile(r"[\u30a0-\u30ffー\u4e00-\u9fff\u3040-\u309f]{2,}")
_US_STATES = ["California", "New York", "Texas", "Washington", "Oregon", "Florida", "Illinois", "Massachusetts",
              "Hawaii", "Colorado", "Arizona", "Nevada"]
DEFAULT_DESTINATION = "California, US"
MAX_CANDIDATES = 14


class ShoppingAgent:
    def __init__(self, store: Store, log: ActivityLog, llm: LLMClient, memory: MemoryService,
                 discovery: XDiscoveryAgent, marketplace: MarketplaceService, costs: LandedCostEstimator,
                 ranker: Ranker, purchase: PurchaseService, sie: SIEAdapter, senso: SensoAdapter,
                 fetch_max_per_run: int = 12):
        self.store = store
        self.log = log
        self.llm = llm
        self.memory = memory
        self.discovery = discovery
        self.marketplace = marketplace
        self.costs = costs
        self.ranker = ranker
        self.purchase = purchase
        self.sie = sie
        self.senso = senso
        self.fetch_max_per_run = fetch_max_per_run

    def integrations(self) -> list[dict[str, Any]]:
        return [self.sie.state.to_json(), self.memory.cognee.state.to_json(), self.senso.state.to_json(),
                self.llm.status()]

    # ---- request parsing -------------------------------------------------
    async def parse_request(self, message: str, run_id: str | None = None) -> ParsedRequest | None:
        raw = sanitize_untrusted(message, 1000).strip()
        m = _BUDGET.search(raw)
        budget = float((m.group(1) or m.group(2)).replace(",", "")) if m else None
        dest = DEFAULT_DESTINATION
        for state in _US_STATES:
            if re.search(rf"\b{state}\b", raw, re.I):
                dest = f"{state}, US"
                break
        facts = self.memory._facts_from_text(raw)
        stated = [a for a, polarity in facts if polarity == "like" and a != "limited"]
        avoided = [a for a, polarity in facts if polarity == "avoid"]
        wants_social = not re.search(r"\b(no social|marketplace only|without (x|twitter))\b", raw, re.I)
        category = detect_category(raw)
        method = "heuristic"
        if category:
            terms = list(CATEGORIES[category]["ja"])
            label = CATEGORIES[category]["label"]
        else:
            # Japanese words typed by the user are usable search terms as-is.
            terms = list(dict.fromkeys(_JA_WORD.findall(nfkc(raw))))[:2]
            label = terms[0] if terms else ""
            category = "custom"
            if not terms and self.llm.available:
                data = await self.llm.complete_json(
                    UNTRUSTED_SYSTEM_RULE + " Return JSON {\"jaTerms\": [up to 2 Japanese search keywords for the "
                    "product the user wants], \"label\": short English product label}.",
                    wrap_untrusted(raw, "user_request"), max_tokens=200, purpose="request_parse", run_id=run_id)
                if data and isinstance(data.get("jaTerms"), list):
                    terms = [str(t)[:30] for t in data["jaTerms"] if isinstance(t, str) and _JA_WORD.search(t)][:2]
                    label = str(data.get("label") or (terms[0] if terms else ""))[:40]
                    method = "llm"
            if not terms:
                return None
        return ParsedRequest(raw=raw, category=category, category_label=label, ja_terms=terms, budget_usd=budget,
                             destination=dest, wants_social=wants_social, stated_preferences=stated,
                             stated_avoidances=avoided, parse_method=method)

    # ---- candidates ------------------------------------------------------
    @staticmethod
    def lead_to_candidate(lead: SocialProductLead) -> Candidate:
        sf = lead.storefront
        resolved = bool(sf and sf.status == "RESOLVED_PRODUCT")
        title = (sf.product_title if resolved and sf.product_title else None) or lead.product_name or \
                (lead.english_translation or lead.original_japanese_text)[:60]
        prov = lead.provenance
        label = "X announcement" + (" (FIXTURE sample)" if prov.is_fixture else
                                    f" via {str(prov.provider).replace('_', ' ').lower()}")
        return Candidate(
            id=f"cand_{lead.id}", source="SOCIAL_DROP", source_label=label, title=title,
            description=(sf.description_excerpt if resolved else None) or lead.english_translation
                        or lead.original_japanese_text[:300],
            price_jpy=(sf.price_jpy if resolved and sf.price_jpy is not None else lead.announced_price_jpy),
            purchase_url=sf.purchase_url if resolved else None, announcement_url=lead.source_post_url,
            store_type=sf.store_type if sf else None,
            store_name=(sf.store_name if sf else None) or (STORE_LABELS.get(sf.store_type or "") if sf else None),
            availability=lead.availability_status, attributes=lead.attributes,
            image_url=lead.media_urls[0] if lead.media_urls else None, is_fixture=prov.is_fixture, lead_id=lead.id,
            verification=str(lead.verification_status),
            ships_internationally=sf.ships_internationally if resolved else "unknown",
            restrictions=list(sf.restrictions) if resolved else [], retrieved_at=sf.fetched_at if sf else lead.retrieval_timestamp,
            injection_flags=lead.injection_flags,
        )

    # ---- main ------------------------------------------------------------
    async def run(self, message: str, user_id: str = "default", *,
                  prepare_draft: bool = True, refresh_imports: bool = False,
                  learn_preferences: bool = True) -> AgentRun | dict[str, Any]:
        run_id = new_id("run")
        self.log.log("request", f"Request received: {message[:200]}", "info", run_id)
        req = await self.parse_request(message, run_id)
        if req is None:
            msg = ("I could not tell which product to search for. I know these categories: "
                   + ", ".join(v["label"] for v in CATEGORIES.values())
                   + ". You can also include the Japanese product word (for example 香炉) and I will search for it.")
            self.log.log("request", msg, "warn", run_id)
            return {"id": run_id, "error": "unrecognized_request", "message": msg,
                    "timeline": list(reversed(self.store.events(run_id)))}
        self.log.log("request", f"Understood: {req.category_label} · budget "
                                f"{('$%.0f delivered' % req.budget_usd) if req.budget_usd is not None else 'not stated'} · "
                                f"to {req.destination} · Japanese terms: {', '.join(req.ja_terms)}"
                                + (f" · wants: {', '.join(ATTRIBUTES[a]['label'] for a in req.stated_preferences)}"
                                   if req.stated_preferences else ""),
                     "ok", run_id, {"parsed": req.to_json()})

        # A request that states a lasting preference ("I prefer matte") is remembered.
        if learn_preferences and self.memory.looks_like_preference(req.raw) and re.search(r"\b(i|my|me)\b", req.raw, re.I) \
                and re.search(r"\b(prefer|always|never|don'?t like|dislike|hate|love|i (really |only )?like)\b", req.raw, re.I):
            await self.memory.add_preference(req.raw, user_id, source="learned_from_request", run_id=run_id)

        history = self.memory.past_searches(user_id)
        mem = await self.memory.recall(f"{req.category_label} {req.raw}", user_id, run_id)
        self.log.log("memory", f"Memory ({mem.backend}): "
                               + (f"prefers {', '.join(ATTRIBUTES[a]['label'] for a in mem.preferred)}" if mem.preferred else "no stored likes")
                               + (f"; avoids {', '.join(ATTRIBUTES[a]['label'] for a in mem.avoided)}" if mem.avoided else "")
                               + f"; {len(history)} past search(es).",
                     "ok" if mem.backend == "cognee" else "info", run_id,
                     {"backend": mem.backend, "reason": mem.backend_reason, "preferred": mem.preferred, "avoided": mem.avoided})
        self.memory.record_search(req.raw, req.category, user_id)

        run = AgentRun(id=run_id, request=req, memory=mem)
        budget = FetchBudget(self.fetch_max_per_run)
        preferred, _avoided = req.preference_signals(mem)

        # ---- discovery ---------------------------------------------------
        leads: list[SocialProductLead] = []
        if req.wants_social:
            run.discovery = await self.discovery.discover(
                req.category, preferred, history, run_id=run_id, budget=budget,
                ja_terms=req.ja_terms if req.category == "custom" else None)
            leads = run.discovery.leads

        # ---- marketplace -------------------------------------------------
        market, sources = await self.marketplace.search(req.category, req.ja_terms, run_id,
                                                      refresh_imports=refresh_imports, budget=budget)
        run.marketplace_sources = sources
        run.marketplace_count = len(market)

        candidates = [self.lead_to_candidate(l) for l in leads] + market
        candidates = candidates[:MAX_CANDIDATES]
        lead_by_cand = {f"cand_{l.id}": l for l in leads}
        claimed_sold_out = {cid for cid, l in lead_by_cand.items()
                            if str(l.announcement_type) == "SOLD_OUT" and str(l.availability_status) == Availability.UNKNOWN.value}
        self.log.log("candidates", f"Comparing {len(candidates)} candidate(s): {len(leads)} from X announcements, "
                                   f"{len(market)} from marketplace sources.", "ok" if candidates else "warn", run_id)

        # ---- cost --------------------------------------------------------
        costs = {}
        for c in candidates:
            lead = lead_by_cand.get(c.id)
            observed = not (lead is not None and (lead.storefront is None or lead.storefront.status != "RESOLVED_PRODUCT"
                                                  or lead.storefront.price_jpy is None))
            costs[c.id] = await self.costs.estimate(c, req.category, req.budget_usd, req.destination, observed, run_id)
        priced = [c for c in costs.values() if c.total_usd is not None]
        if priced:
            within = sum(1 for c in priced if c.within_budget == "yes")
            self.log.log("cost", f"Estimated landed cost for {len(priced)} candidate(s) to {req.destination}"
                                 + (f"; {within} within ${req.budget_usd:.0f}." if req.budget_usd is not None else "."),
                         "ok", run_id, {"fxSource": priced[0].fx_source, "citationBackend": priced[0].citation_backend})

        # ---- ranking -----------------------------------------------------
        run.ranked = await self.ranker.rank(req, mem, candidates, costs, claimed_sold_out, run_id)

        # ---- draft + stop ------------------------------------------------
        def affordable(r: RankedCandidate) -> bool:
            return r.cost is None or r.cost.within_budget != "no"

        def fits_taste(r: RankedCandidate) -> bool:
            return not set(r.candidate.attributes).intersection(_avoided)

        pick = next((r for r in run.ranked if r.purchase_path == "PROXY_REQUEST_DRAFT"
                     and affordable(r) and fits_taste(r)), None)
        if not prepare_draft:
            run.manual_next_steps = ["Hunt checked without preparing another draft. Review the comparison "
                                     "and prepare a draft for an item when you want one."]
            self.log.log("draft", "Saved hunt check: no draft created.", "info", run_id)
        elif pick is not None:
            run.draft = self.purchase.create_draft(pick, req, lead_by_cand.get(pick.candidate.id), run_id)
        else:
            over = next((r for r in run.ranked if r.purchase_path == "PROXY_REQUEST_DRAFT"
                         and not affordable(r) and fits_taste(r)), None)
            manual = next((r for r in run.ranked if r.purchase_path == "MANUAL_ONLY"
                           and affordable(r) and fits_taste(r)), None)
            steps: list[str] = []
            if over is not None:
                steps.append(f"The best purchasable match, {over.candidate.title}, is estimated at "
                             f"${over.cost.total_usd:.2f} delivered, over your budget. Raise the budget or ask for a "
                             "draft from the ranked list if you still want it.")
            if manual is not None:
                run.draft = self.purchase.create_draft(manual, req, lead_by_cand.get(manual.candidate.id), run_id)
                steps.append(f"Manual purchase notes were prepared for {manual.candidate.title}: {manual.purchase_path_reason}")
            if not run.ranked:
                steps.append("Nothing was found. Configure X or Brave Search and marketplace credentials, "
                             "or import a real post or listing URL.")
            elif not steps:
                steps.append("No candidate meets your preferences and purchase requirements right now. Add creators to your "
                             "watch list and refresh Social Drops later.")
            run.manual_next_steps = steps
            self.log.log("draft", "No supported nonbinding purchase draft could be prepared; manual next steps provided.",
                         "warn", run_id, {"steps": steps})

        run.summary = self._summarize(run)
        self.log.log("stop", "Stopped before any binding action. No order, bid or payment was made; "
                             + ("a draft is waiting for your review." if run.draft else "there is nothing to approve."),
                     "ok", run_id, {"binding": False, "draftId": run.draft.id if run.draft else None})
        run.integrations = self.integrations()
        run.timeline = [TimelineEvent.model_validate(e) for e in reversed(self.store.events(run_id))]
        self.store.put("run", run.id, run.to_json())
        return run

    def _summarize(self, run: AgentRun) -> str:
        req = run.request
        parts: list[str] = []
        d = run.discovery
        if d is not None:
            n = len(d.leads)
            fx = sum(1 for l in d.leads if l.provenance.is_fixture)
            if d.fresh_discovery:
                parts.append(f"Found {n} relevant X announcement lead(s) through "
                             f"{str(d.selected_provider).replace('_', ' ').lower()}"
                             + (f" (fewer than the 3 targeted)" if n < 3 else "") + ".")
            elif n:
                parts.append(f"No live X source returned posts, so this is not fresh X discovery. Showing {n} lead(s)"
                             + (f", {fx} of them bundled fixtures (sample data)" if fx else " from posts you imported") + ".")
            else:
                parts.append("No X announcement leads were found.")
            parts.append(f"{d.stats.storefront_links_resolved} store listing(s) resolved; "
                         f"{d.stats.verified_purchasable} purchasable now.")
        parts.append(f"Compared against {run.marketplace_count} marketplace listing(s).")
        if run.ranked:
            top = run.ranked[0]
            cost = f"about ${top.cost.total_usd:.2f} delivered" if top.cost and top.cost.total_usd is not None else "cost unknown"
            parts.append(f"Top match: {top.candidate.title} ({cost}"
                         + (", sample data" if top.candidate.is_fixture else "") + ").")
        if run.draft:
            kind = "A nonbinding proxy request draft" if run.draft.path == "PROXY_REQUEST_DRAFT" else "Manual purchase notes"
            parts.append(f"{kind} for {run.draft.title} is waiting for your review.")
        else:
            parts.append("No draft was prepared; see the manual next steps.")
        parts.append("Nothing has been ordered.")
        if req.budget_usd is None:
            parts.append("No budget was stated, so candidates were not filtered by cost.")
        return " ".join(parts)
