"""The required test case, end to end:

  "Find me Japanese artisan keycaps announced recently on X, under $120 delivered to California."

Run twice: once with no credentials (labeled fixtures, no claim of fresh
discovery), and once against a simulated X API to exercise the live path."""

import httpx

from kai.models import AgentRun

from .conftest import REQUIRED_CASE, live_handler

PREF = "I prefer handmade pieces with a matte finish, not glossy"


async def test_required_case_offline_with_labeled_fixtures(make_kai):
    k, net = make_kai()
    await k.memory.add_preference(PREF)
    run = await k.agent.run(REQUIRED_CASE)
    assert isinstance(run, AgentRun)
    d = run.discovery

    # request understanding
    assert run.request.category == "keycaps" and run.request.budget_usd == 120
    assert run.request.destination == "California, US"

    # A. Japanese queries
    assert 3 <= len(d.queries) <= 6 and all(q.valid for q in d.queries)
    assert all("キーキャップ" in q.text for q in d.queries)

    # B. real discovery was attempted and honestly reported as unavailable
    states = {s.provider: s for s in d.provider_statuses}
    assert states["X_OFFICIAL_API"].state == "unconfigured" and "no official X API request was made" in states["X_OFFICIAL_API"].error
    assert states["INDEXED_WEB_SEARCH"].state == "unconfigured"
    assert states["FIXTURE"].state == "ok" and states["FIXTURE"].selected and states["FIXTURE"].is_fixture
    assert d.fresh_discovery is False and d.selected_provider == "FIXTURE"
    assert "not fresh X discovery" in run.summary
    assert all(s.retrieved_at for s in d.provider_statuses if s.state == "ok")
    assert all(s.coverage for s in d.provider_statuses if s.provider != "MANUAL_IMPORT" or s.coverage)
    assert net.requests == [], "no network call of any kind without credentials"

    # C. at least 3 relevant leads, all labeled as fixtures
    assert len(d.leads) >= 3
    assert all(l.provenance.is_fixture and l.source_post_url.startswith("https://x.kai-fixture.invalid/") for l in d.leads)

    # D. translated and classified
    assert all(l.english_translation and l.provenance.translation_method == "fixture_provided" for l in d.leads)
    types = {l.announcement_type for l in d.leads}
    assert {"NEW_RELEASE", "LOTTERY", "PREORDER", "SOLD_OUT"} <= types
    assert all(l.original_japanese_text for l in d.leads)

    # E. storefront resolved; announcement URL and purchase URL are different things
    resolved = [l for l in d.leads if l.verification_status == "LISTING_RESOLVED"]
    assert resolved
    for l in resolved:
        assert l.storefront.purchase_url and l.storefront.purchase_url != l.source_post_url
        assert "x.kai-fixture.invalid" not in l.storefront.purchase_url
    for l in d.leads:
        if l.verification_status != "LISTING_RESOLVED":
            assert l.storefront is None or l.storefront.purchase_url is None, "no product link is ever invented"
    assert d.stats.duplicates_filtered >= 1 and d.stats.not_useful >= 1
    assert d.stats.blocked_or_unsupported >= 1
    lottery = next(l for l in d.leads if l.announcement_type == "LOTTERY")
    assert lottery.availability_status == "LOTTERY" and lottery.lottery_deadline

    # F. compared against marketplace results
    sources = {r.candidate.source for r in run.ranked}
    assert {"SOCIAL_DROP", "MARKETPLACE"} <= sources and run.marketplace_count >= 3
    assert all(r.candidate.is_fixture for r in run.ranked)

    # G. stored preferences and conservative costs applied
    assert run.memory.backend == "fallback" and set(run.memory.preferred) == {"handmade", "matte"}
    top = run.ranked[0]
    assert {"handmade", "matte"} <= set(top.candidate.attributes)
    assert top.cost.within_budget == "yes" and top.cost.total_usd <= 120 and top.cost.citations
    assert "not semantic" in top.score.ranking_backend
    glossy = next(r for r in run.ranked if "glossy" in r.candidate.attributes and "matte" not in r.candidate.attributes)
    assert glossy.rank > top.rank
    by_avail = {r.candidate.availability: r for r in run.ranked}
    assert by_avail["SOLD_OUT"].purchase_path == "WATCH_ONLY"
    assert by_avail["LOTTERY"].purchase_path == "MANUAL_ONLY"
    assert by_avail["SOLD_OUT"].rank > top.rank

    # H. nonbinding draft with the full evidence chain
    draft = run.draft
    assert draft is not None and draft.path == "PROXY_REQUEST_DRAFT" and draft.binding is False
    assert draft.candidate_id == top.candidate.id
    assert [s.stage for s in draft.evidence_chain] == ["X_POST", "CREATOR", "STORE", "PRODUCT", "LANDED_COST", "HUMAN_REVIEW"]
    assert draft.purchase_url != draft.announcement_url
    assert any("SAMPLE DATA" in w for w in draft.warnings)

    # I. stopped before anything binding
    assert draft.status == "PENDING_REVIEW" and run.stopped_before_binding_action is True
    assert "Nothing has been ordered" in run.summary

    # J. source-to-decision timeline
    stages = [e.stage for e in run.timeline]
    for stage in ["request", "memory", "x_queries", "x_provider", "x_extract", "storefront", "x_dedupe", "x_discovery",
                  "marketplace", "cost", "ranking", "draft", "stop"]:
        assert stage in stages, stage
    assert stages.index("x_queries") < stages.index("storefront") < stages.index("ranking") < stages.index("draft") < stages.index("stop")
    assert run.timeline[-1].stage == "stop" and "No order, bid or payment" in run.timeline[-1].message


async def test_prompt_injection_on_a_product_page_does_not_change_the_outcome(make_kai):
    k, _ = make_kai()
    await k.memory.add_preference(PREF)
    run = await k.agent.run(REQUIRED_CASE)
    poisoned = next(r for r in run.ranked if r.candidate.injection_flags)
    assert "forced_purchase" in poisoned.candidate.injection_flags
    assert run.draft.candidate_id != poisoned.candidate.id, "the page that told the agent to buy it did not win"
    assert poisoned.score.trust < run.ranked[0].score.trust
    assert any("instruction-like" in r for r in poisoned.score.reasons)
    assert run.draft.status == "PENDING_REVIEW" and len(k.purchase.list()) == 1
    assert any(e.stage == "storefront" and "flagged and ignored" in e.message for e in run.timeline)


async def test_required_case_against_simulated_x_api(make_kai):
    k, net = make_kai(live_handler(), x_bearer_token="token", x_max_queries_per_refresh=1)
    await k.memory.add_preference(PREF)
    run = await k.agent.run(REQUIRED_CASE)
    d = run.discovery

    assert d.fresh_discovery is True and d.selected_provider == "X_OFFICIAL_API"
    states = {s.provider: s for s in d.provider_statuses}
    assert states["X_OFFICIAL_API"].state == "ok" and states["X_OFFICIAL_API"].selected
    assert states["INDEXED_WEB_SEARCH"].state == "skipped" and states["FIXTURE"].state == "skipped"
    assert len(d.leads) == 3 and not any(l.provenance.is_fixture for l in d.leads)
    assert "not fresh X discovery" not in run.summary

    by_handle = {l.author_handle: l for l in d.leads}
    souten = by_handle["souten_keys"]
    assert souten.verification_status == "LISTING_RESOLVED"
    assert souten.storefront.purchase_url == "https://souten-keys.booth.pm/items/555"
    assert souten.source_post_url == "https://x.com/souten_keys/status/1900000000000000001"
    assert souten.storefront.price_jpy == 7200 and souten.availability_status == "IN_STOCK"
    assert souten.english_translation is None, "no LLM configured: no translation is claimed"
    assert souten.glossary

    assert by_handle["muhyo_resin"].availability_status == "LOTTERY"
    momiji = by_handle["momiji_caps"]
    assert momiji.verification_status == "ANNOUNCEMENT_ONLY" and momiji.storefront.status == "NO_LINK"
    assert momiji.availability_status == "UNKNOWN", "a restock post is not proof of stock"

    # only the official API and the two linked stores were contacted; x.com itself never
    assert {r.url.host for r in net.requests} == {"api.x.com", "souten-keys.booth.pm", "muhyo.thebase.in"}

    assert run.draft.candidate_id == "cand_" + souten.id and run.draft.is_fixture is False
    assert run.draft.evidence_chain[0].url == souten.source_post_url
    assert run.draft.evidence_chain[3].url == souten.storefront.purchase_url
    paths = {r.candidate.lead_id: r.purchase_path for r in run.ranked if r.candidate.lead_id}
    assert paths[by_handle["muhyo_resin"].id] == "MANUAL_ONLY" and paths[momiji.id] == "MANUAL_ONLY"
    assert k.official.spent() > 0


async def test_x_api_401_is_reported_and_demo_stays_functional_without_claiming_fresh_results(make_kai):
    k, net = make_kai(live_handler(x_status=401), x_bearer_token="expired")
    run = await k.agent.run(REQUIRED_CASE)
    d = run.discovery
    st = next(s for s in d.provider_statuses if s.provider == "X_OFFICIAL_API")
    assert st.state == "unauthorized" and st.http_status == 401 and not st.selected
    assert len(net.to("api.x.com")) == 1
    assert d.fresh_discovery is False and d.selected_provider == "FIXTURE"
    assert all(l.provenance.is_fixture for l in d.leads)
    assert "not fresh X discovery" in run.summary
    assert any(e.status == "error" and "401" in e.message for e in run.timeline)
    assert run.draft is not None and run.draft.is_fixture


async def test_fewer_than_three_leads_is_stated_when_fixtures_are_disabled(make_kai):
    body = {"data": [live_body_post()], "includes": {"users": [{"id": "u1", "username": "souten_keys", "name": "蒼天工房"}]}}
    k, _ = make_kai(live_handler(x_body=body), x_bearer_token="t", allow_fixtures=False, x_max_queries_per_refresh=1)
    run = await k.agent.run(REQUIRED_CASE)
    assert len(run.discovery.leads) == 1
    assert any("fewer than the 3 targeted" in n for n in run.discovery.notes)
    assert "Found 1 relevant X announcement lead(s)" in run.summary and "fewer than the 3 targeted" in run.summary
    assert not any(r.candidate.is_fixture for r in run.ranked)


def live_body_post() -> dict:
    from .conftest import X_SEARCH_BODY
    return X_SEARCH_BODY["data"][0]


async def test_nothing_found_gives_manual_next_steps_not_a_draft(make_kai):
    k, _ = make_kai(allow_fixtures=False)
    run = await k.agent.run(REQUIRED_CASE)
    assert run.discovery.leads == [] and run.ranked == [] and run.draft is None
    assert run.manual_next_steps and run.stopped_before_binding_action
    assert any("X_BEARER_TOKEN" in s or "import" in s.lower() for s in run.manual_next_steps)


async def test_unrecognized_request_is_declined_cleanly(make_kai):
    k, _ = make_kai()
    result = await k.agent.run("hello there")
    assert isinstance(result, dict) and result["error"] == "unrecognized_request"


async def test_stated_preference_in_request_is_remembered_for_next_time(make_kai):
    k, _ = make_kai()
    await k.agent.run("I like matte finishes. Find me artisan keycaps from Japan under $150 to California")
    assert any(p["attribute"] == "matte" for p in k.memory.list_preferences())
    searches = k.memory.past_searches()
    assert searches and searches[0]["category"] == "keycaps"


async def test_llm_request_never_contains_untrusted_text_outside_the_data_wrapper(make_kai):
    import json
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})

    k, _ = make_kai(handler, openai_api_key="sk")
    await k.agent.run(REQUIRED_CASE)
    post_calls = [b for b in seen if "<untrusted" in b["messages"][1]["content"]]
    assert post_calls, "posts are sent to the model only inside an untrusted-data wrapper"
    for b in seen:
        assert "tools" not in b and "functions" not in b, "the model is never given tools it could act with"
