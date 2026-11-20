import inspect

import httpx
import pytest

import kai.purchase as purchase_module
from kai.models import Candidate, DraftStatus, ParsedRequest, RankedCandidate, ScoreBreakdown
from kai.purchase import BINDING_ACTIONS_SUPPORTED
from kai.ranking import purchase_path

REQ = ParsedRequest(raw="keycaps", category="keycaps", category_label="Artisan keycaps", ja_terms=["キーキャップ"], budget_usd=120)


def cand(**kw) -> Candidate:
    base = dict(id="c1", source="SOCIAL_DROP", source_label="X announcement", title="蒼天 キーキャップ", price_jpy=8800,
                purchase_url="https://a.booth.pm/items/1", announcement_url="https://x.com/a/status/1",
                store_type="booth", store_name="蒼天工房", availability="IN_STOCK", verification="LISTING_RESOLVED",
                retrieved_at="2026-11-20T00:00:00+00:00")
    base.update(kw)
    return Candidate(**base)


# ---------------------------------------------------------------- landed cost


async def test_landed_cost_lines_sum_and_cite_sources(make_kai):
    k, net = make_kai()
    cost = await k.costs.estimate(cand(), "keycaps", 120, "California, US", True)
    lines = {l.key: l for l in cost.lines}
    assert set(lines) == {"item", "domestic_shipping", "proxy_fee", "intl_shipping", "duty", "clearance", "use_tax", "fx_buffer"}
    assert cost.total_usd == pytest.approx(sum(l.amount_usd for l in cost.lines), abs=0.02)
    # item: 8800 JPY at the configured fallback rate of 0.0070
    assert lines["item"].amount_usd == pytest.approx(61.60) and lines["item"].confidence == "observed"
    # duty: 12.5% of the item value
    assert lines["duty"].amount_usd == pytest.approx(7.70) and lines["duty"].confidence == "sourced"
    # clearance: $1.69 + 10% of duty
    assert lines["clearance"].amount_usd == pytest.approx(2.46)
    assert lines["use_tax"].confidence == "assumption" and lines["proxy_fee"].confidence == "assumption"

    cited = {c.id: c for c in cost.citations}
    for line in cost.lines:
        if line.confidence == "sourced":
            assert line.citation_ids, f"{line.key} is marked sourced but cites nothing"
        assert all(cid in cited for cid in line.citation_ids)
    assert all(c.url and c.url.startswith("https://") and c.retrieved_at and c.excerpt for c in cost.citations)
    assert cost.citation_backend == "bundled" and all(c.via == "bundled" for c in cost.citations)
    assert "not a live rate" in cost.fx_source and cost.fx_as_of is None
    assert cost.within_budget == "yes" and cost.conservative
    assert net.requests == []


async def test_budget_exceeded_and_unknown_price(make_kai):
    k, _ = make_kai()
    over = await k.costs.estimate(cand(price_jpy=15000), "keycaps", 120, "California, US", True)
    assert over.within_budget == "no" and over.total_usd > 120
    unknown = await k.costs.estimate(cand(price_jpy=None), "keycaps", 120, "California, US", False)
    assert unknown.total_usd is None and unknown.within_budget == "unknown" and unknown.lines == []
    assert unknown.warnings


async def test_announced_price_is_marked_unverified(make_kai):
    k, _ = make_kai()
    cost = await k.costs.estimate(cand(verification="ANNOUNCEMENT_ONLY"), "keycaps", 120, "California, US", False)
    item = next(l for l in cost.lines if l.key == "item")
    assert item.confidence != "observed"
    assert "not confirmed on a store page" in item.basis
    assert any("from the post" in w for w in cost.warnings)


async def test_japan_only_shipping_is_called_out(make_kai):
    k, _ = make_kai()
    cost = await k.costs.estimate(cand(ships_internationally="no"), "keycaps", 120, "California, US", True)
    assert any("proxy" in w.lower() or "forward" in w.lower() for w in cost.warnings)


async def test_live_fx_rate_is_used_and_labeled_when_available(make_kai):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.frankfurter.dev" and request.url.params["base"] == "JPY"
        return httpx.Response(200, json={"base": "JPY", "date": "2026-11-19", "rates": {"USD": 0.0066}})

    k, net = make_kai(handler, fx_live=True)
    cost = await k.costs.estimate(cand(), "keycaps", None, "California, US", True)
    assert cost.fx_rate_usd_per_jpy == 0.0066 and "live" in cost.fx_source and cost.fx_as_of == "2026-11-19"
    await k.costs.estimate(cand(), "keycaps", None, "California, US", True)
    assert len(net.requests) == 1, "FX rate is cached"


async def test_fx_failure_and_implausible_rate_fall_back_with_label(make_kai):
    for resp in (httpx.Response(500), httpx.Response(200, json={"rates": {"USD": 7.0}})):
        k, _ = make_kai(lambda r, resp=resp: resp, fx_live=True)
        cost = await k.costs.estimate(cand(), "keycaps", None, "California, US", True)
        assert cost.fx_rate_usd_per_jpy == 0.0070 and "not a live rate" in cost.fx_source


async def test_non_us_destination_is_not_estimated_with_us_rules(make_kai):
    k, _ = make_kai()
    cost = await k.costs.estimate(cand(), "keycaps", 120, "Berlin, Germany", True)
    assert cost.total_usd is None and cost.lines == [] and cost.within_budget == "unknown"
    assert any("only implemented for U.S. destinations" in w for w in cost.warnings)


# ---------------------------------------------------------------- purchase paths


def test_purchase_paths():
    assert purchase_path(cand())[0] == "PROXY_REQUEST_DRAFT"
    assert purchase_path(cand(availability="PREORDER"))[0] == "PROXY_REQUEST_DRAFT"
    assert purchase_path(cand(availability="LOTTERY"))[0] == "MANUAL_ONLY"
    assert purchase_path(cand(availability="SOLD_OUT"))[0] == "WATCH_ONLY"
    assert purchase_path(cand(), claimed_sold_out=True)[0] == "WATCH_ONLY"
    assert purchase_path(cand(availability="UNKNOWN"))[0] == "MANUAL_ONLY"
    assert purchase_path(cand(purchase_url=None, verification="ANNOUNCEMENT_ONLY"))[0] == "MANUAL_ONLY"
    assert purchase_path(cand(verification="BLOCKED"))[0] == "MANUAL_ONLY"
    assert purchase_path(cand(price_jpy=None))[0] == "MANUAL_ONLY"
    path, why = purchase_path(cand(store_type="yahoo_auctions"))
    assert path == "MANUAL_ONLY" and "bid" in why


async def _rc(k, c: Candidate) -> RankedCandidate:
    path, why = purchase_path(c)
    cost = await k.costs.estimate(c, "keycaps", 120, "California, US", True)
    return RankedCandidate(candidate=c, score=ScoreBreakdown(), cost=cost, purchase_path=path, purchase_path_reason=why)


async def test_draft_is_nonbinding_and_waits_for_review(make_kai):
    k, net = make_kai()
    draft = k.purchase.create_draft(await _rc(k, cand()), REQ, None, "run_1")
    assert draft.binding is False and draft.status == "PENDING_REVIEW" and draft.path == "PROXY_REQUEST_DRAFT"
    assert "not submitted by KAI" in draft.request_text and "https://a.booth.pm/items/1" in draft.request_text
    assert [s.stage for s in draft.evidence_chain][-2:] == ["LANDED_COST", "HUMAN_REVIEW"]
    assert any("only you can take" in s for s in draft.manual_steps)
    assert net.requests == [], "preparing a draft contacts no one"


async def test_review_gate_approve_and_reject_do_not_contact_any_service(make_kai):
    k, net = make_kai()
    d1 = k.purchase.create_draft(await _rc(k, cand()), REQ, None)
    d2 = k.purchase.create_draft(await _rc(k, cand(id="c2")), REQ, None)
    approved = k.purchase.review(d1.id, "approve", "looks good")
    assert approved.status == "APPROVED_FOR_MANUAL_HANDOFF" and approved.binding is False and approved.reviewed_at
    assert k.purchase.review(d2.id, "reject").status == "REJECTED"
    with pytest.raises(ValueError):
        k.purchase.review(d1.id, "approve")  # already reviewed
    with pytest.raises(ValueError):
        k.purchase.review(d2.id, "buy")
    with pytest.raises(KeyError):
        k.purchase.review("missing", "approve")
    assert net.requests == []


async def test_lottery_gets_manual_notes_and_sold_out_gets_nothing(make_kai):
    k, _ = make_kai()
    lottery = k.purchase.create_draft(await _rc(k, cand(availability="LOTTERY")), REQ, None)
    assert lottery.path == "MANUAL_ONLY" and "MANUAL PURCHASE NOTES" in lottery.request_text
    assert any("does not enter lotteries" in s for s in lottery.manual_steps)
    with pytest.raises(ValueError):
        k.purchase.create_draft(await _rc(k, cand(availability="SOLD_OUT")), REQ, None)


async def test_injection_flags_and_restrictions_surface_as_warnings(make_kai):
    k, _ = make_kai()
    c = cand(injection_flags=["forced_purchase"], restrictions=["No overseas shipping"], is_fixture=True)
    draft = k.purchase.create_draft(await _rc(k, c), REQ, None)
    joined = " ".join(draft.warnings)
    assert "addressed to AI agents" in joined and "No overseas shipping" in joined and "SAMPLE DATA" in joined
    assert "Quantity: 1" in draft.request_text


def test_no_binding_capability_exists_in_the_purchase_module():
    assert BINDING_ACTIONS_SUPPORTED == ()
    assert {s.value for s in DraftStatus} == {"PENDING_REVIEW", "APPROVED_FOR_MANUAL_HANDOFF", "REJECTED"}
    source = inspect.getsource(purchase_module)
    for forbidden in ("httpx", "requests", "urllib", "aiohttp", "socket", "subprocess"):
        assert f"import {forbidden}" not in source, f"purchase module must not be able to reach the network ({forbidden})"
    public = [n for n, _ in inspect.getmembers(purchase_module.PurchaseService, inspect.isfunction) if not n.startswith("_")]
    assert sorted(public) == ["build_evidence_chain", "create_draft", "get", "list", "review"]


async def test_new_draft_replaces_unreviewed_draft_for_same_item_but_keeps_reviewed_ones(make_kai):
    k, _ = make_kai()
    first = k.purchase.create_draft(await _rc(k, cand()), REQ, None)
    second = k.purchase.create_draft(await _rc(k, cand()), REQ, None)
    assert [d.id for d in k.purchase.list()] == [second.id] and k.purchase.get(first.id) is None
    k.purchase.review(second.id, "reject")
    third = k.purchase.create_draft(await _rc(k, cand()), REQ, None)
    assert {d.id for d in k.purchase.list()} == {second.id, third.id}, "a recorded decision is never deleted"
