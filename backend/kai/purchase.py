"""Purchase drafts and the human review gate.

This module is the only place a purchase is ever represented, and it is
deliberately incapable of buying anything: it has no HTTP client, no payment
or checkout code, and no draft state beyond "approved for manual handoff".
Approval means the human has reviewed the draft and will act on it
themselves, outside KAI."""

from __future__ import annotations

from .models import (DraftStatus, EvidenceStep, ParsedRequest, PurchaseDraft, RankedCandidate, SocialProductLead,
                     new_id, now_iso)
from .observability import ActivityLog
from .store import Store

BINDING_ACTIONS_SUPPORTED: tuple[str, ...] = ()  # none, by design


class PurchaseService:
    def __init__(self, store: Store, log: ActivityLog):
        self.store = store
        self.log = log

    def build_evidence_chain(self, rc: RankedCandidate, lead: SocialProductLead | None) -> list[EvidenceStep]:
        c = rc.candidate
        fx = "fixture" if c.is_fixture else "ok"
        chain: list[EvidenceStep] = []
        if lead is not None:
            prov = lead.provenance
            chain.append(EvidenceStep(
                stage="X_POST", label="Original X post" + (" (FIXTURE sample)" if prov.is_fixture else ""),
                detail=f"{str(lead.announcement_type).replace('_', ' ').title()} · posted {lead.source_timestamp or 'time unknown'} "
                       f"· retrieved {lead.retrieval_timestamp} via {str(prov.provider).replace('_', ' ').lower()}",
                url=lead.source_post_url, status=fx))
            chain.append(EvidenceStep(
                stage="CREATOR", label=f"Creator @{lead.author_handle}" if lead.author_handle else "Creator unknown",
                detail=lead.author_display_name, url=lead.author_profile_url,
                status=fx if lead.author_handle else "missing"))
        else:
            chain.append(EvidenceStep(stage="X_POST", label="Not from an X announcement",
                                      detail=f"Source: {c.source_label}", status="missing"))
        if c.purchase_url:
            chain.append(EvidenceStep(stage="STORE", label=f"Store: {c.store_name or c.store_type or 'unknown'}",
                                      detail=f"Type: {c.store_type or 'unknown'}",
                                      url=(lead.storefront.store_url if lead and lead.storefront else None), status=fx))
            price = f"¥{c.price_jpy:,}" if c.price_jpy is not None else "price unknown"
            chain.append(EvidenceStep(
                stage="PRODUCT", label=f"Product page: {c.title}",
                detail=f"{price} · {str(c.availability).replace('_', ' ').lower()} · read {c.retrieved_at or 'time unknown'}",
                url=c.purchase_url, status="warn" if c.injection_flags else fx))
        else:
            chain.append(EvidenceStep(stage="STORE", label="Store not resolved",
                                      detail=rc.purchase_path_reason, status="missing"))
            chain.append(EvidenceStep(stage="PRODUCT", label="Product page not resolved", status="missing"))
        if rc.cost and rc.cost.total_usd is not None:
            chain.append(EvidenceStep(
                stage="LANDED_COST", label=f"Estimated landed cost ${rc.cost.total_usd:.2f} to {rc.cost.destination}",
                detail=f"{len(rc.cost.citations)} source(s) cited via {rc.cost.citation_backend}; "
                       f"budget ${rc.cost.budget_usd:.0f}: {rc.cost.within_budget}" if rc.cost.budget_usd is not None
                       else f"{len(rc.cost.citations)} source(s) cited via {rc.cost.citation_backend}",
                status="ok" if rc.cost.within_budget != "no" else "warn"))
        else:
            chain.append(EvidenceStep(stage="LANDED_COST", label="Landed cost not estimated",
                                      detail="Price unknown", status="missing"))
        chain.append(EvidenceStep(stage="HUMAN_REVIEW", label="Human review",
                                  detail="Pending your decision. KAI cannot place orders, bids or payments.", status="warn"))
        return chain

    def create_draft(self, rc: RankedCandidate, request: ParsedRequest, lead: SocialProductLead | None,
                     run_id: str | None = None) -> PurchaseDraft:
        c = rc.candidate
        if rc.purchase_path == "WATCH_ONLY":
            raise ValueError("Sold-out items cannot be drafted.")
        price = f"¥{c.price_jpy:,}" if c.price_jpy is not None else "unknown"
        total = f"${rc.cost.total_usd:.2f}" if rc.cost and rc.cost.total_usd is not None else "not estimated"
        warnings: list[str] = []
        if c.is_fixture:
            warnings.append("SAMPLE DATA: this listing is a bundled fixture. The store and product do not exist.")
        if c.injection_flags:
            warnings.append("The product page contained text addressed to AI agents (" + ", ".join(c.injection_flags)
                            + "). It was ignored and had no effect on this draft.")
        warnings.extend(c.restrictions)
        if rc.cost:
            if rc.cost.within_budget == "no":
                warnings.append(f"Estimated total {total} is over your ${rc.cost.budget_usd:.0f} budget.")

        if rc.purchase_path == "PROXY_REQUEST_DRAFT":
            request_text = "\n".join([
                "PROXY PURCHASE REQUEST - DRAFT (not submitted by KAI)",
                f"Item: {c.title}",
                f"Product page: {c.purchase_url}",
                f"Store: {c.store_name or c.store_type or 'unknown'}",
                f"Listed price: {price} (as read {c.retrieved_at or 'time unknown'})",
                "Quantity: 1",
                f"Sale type: {str(c.availability).replace('_', ' ').lower()}",
                f"Ship to: {request.destination} (enter your address with the service)",
                f"My estimated delivered total: {total} (conservative estimate, not a quote)",
                "Instructions: confirm current price and stock before buying. Do not substitute another item. "
                "Stop and ask me if the total would exceed my estimate.",
            ])
            steps = [
                "Open the product page yourself and confirm the price, stock and any purchase limits.",
                "Choose a proxy-buying or forwarding service that accepts requests for this shop by URL. "
                "KAI has not checked that any particular service will accept this shop.",
                "Paste the request text into that service's request form and review their quote.",
                "Paying the service is a binding step that only you can take, outside KAI.",
            ]
            if c.availability in ("PREORDER", "MADE_TO_ORDER"):
                steps.insert(1, "This is a preorder / made-to-order item: check the delivery estimate and whether "
                                "cancellation is possible before committing.")
        else:
            request_text = "\n".join([
                "MANUAL PURCHASE NOTES (KAI cannot draft a proxy request for this item)",
                f"Item: {c.title}",
                f"Announcement: {c.announcement_url or 'n/a'}",
                f"Product page: {c.purchase_url or 'not resolved'}",
                f"Price: {price}",
                f"Why manual: {rc.purchase_path_reason}",
            ])
            steps = [f"Why this is manual-only: {rc.purchase_path_reason}"]
            if c.announcement_url:
                steps.append("Open the original post to check the creator's latest instructions.")
            if c.availability == "LOTTERY":
                steps.append("Lottery entry must be submitted by you on the store's own form, usually with a store "
                             "account. KAI does not enter lotteries.")
            if c.purchase_url:
                steps.append("Open the store page and follow its purchase flow yourself.")
            else:
                steps.append("Ask the creator where the item is sold, or check the links on their profile.")
            steps.append("If the store ships only within Japan you will need a proxy or forwarding service; "
                         "confirm they can handle this store first.")

        draft = PurchaseDraft(
            id=new_id("draft"), candidate_id=c.id, run_id=run_id, path=rc.purchase_path, binding=False,
            title=c.title, purchase_url=c.purchase_url, announcement_url=c.announcement_url,
            request_text=request_text, manual_steps=steps, warnings=warnings,
            evidence_chain=self.build_evidence_chain(rc, lead), cost=rc.cost, is_fixture=c.is_fixture)
        # A newer draft for the same item replaces an unreviewed older one, so the queue has no duplicates.
        for old in self.store.list("draft"):
            if old.get("candidateId") == c.id and old.get("status") == DraftStatus.PENDING_REVIEW.value:
                self.store.delete("draft", old["id"])
        self.store.put("draft", draft.id, draft.to_json())
        self.log.log("draft", f"Prepared nonbinding {'proxy request draft' if draft.path == 'PROXY_REQUEST_DRAFT' else 'manual purchase notes'} "
                              f"for: {c.title[:60]}. Waiting for human review.", "ok", run_id,
                     {"draftId": draft.id, "path": draft.path, "binding": False})
        return draft

    def get(self, draft_id: str) -> PurchaseDraft | None:
        d = self.store.get("draft", draft_id)
        return PurchaseDraft.model_validate(d) if d else None

    def list(self) -> list[PurchaseDraft]:
        return [PurchaseDraft.model_validate(d) for d in self.store.list("draft")]

    def review(self, draft_id: str, decision: str, note: str | None = None) -> PurchaseDraft:
        draft = self.get(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        if decision not in ("approve", "reject"):
            raise ValueError("decision must be 'approve' or 'reject'")
        if draft.status != DraftStatus.PENDING_REVIEW.value:
            raise ValueError(f"Draft was already reviewed ({draft.status}).")
        draft.status = (DraftStatus.APPROVED_FOR_MANUAL_HANDOFF if decision == "approve" else DraftStatus.REJECTED)
        draft.reviewed_at = now_iso()
        draft.reviewer_note = (note or "").strip()[:500] or None
        for step in draft.evidence_chain:
            if step.stage == "HUMAN_REVIEW":
                step.status = "ok" if decision == "approve" else "missing"
                step.detail = ("Approved by you for manual handoff. KAI took no purchase action; the next steps are yours."
                               if decision == "approve" else "Rejected by you. Nothing further will happen.")
        self.store.put("draft", draft.id, draft.to_json())
        self.log.log("review", f"Human {'approved' if decision == 'approve' else 'rejected'} draft for: {draft.title[:60]}. "
                               "KAI placed no order, bid or payment.", "ok", draft.run_id,
                     {"draftId": draft.id, "status": draft.status, "binding": False})
        return draft
