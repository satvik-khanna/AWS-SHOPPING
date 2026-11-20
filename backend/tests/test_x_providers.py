import httpx
import pytest

from kai.models import XQuery
from kai.xdiscovery.providers import parse_status_url
from kai.xdiscovery.query_engine import build_queries

from .conftest import X_SEARCH_BODY, live_handler

QUERIES = build_queries("keycaps")


async def test_unconfigured_official_provider_makes_no_request(make_kai):
    k, net = make_kai()
    posts, st = await k.official.search(QUERIES)
    assert posts == [] and st.state == "unconfigured"
    assert "X_BEARER_TOKEN" in st.error
    assert net.requests == []


async def test_official_provider_parses_entities_and_drops_reposts(make_kai):
    k, net = make_kai(live_handler(), x_bearer_token="test-token", x_max_queries_per_refresh=1)
    posts, st = await k.official.search(QUERIES)
    assert st.state == "ok" and st.queries_run == 1 and st.http_status == 200
    req = net.to("api.x.com")[0]
    assert req.url.path == "/2/tweets/search/recent"
    assert req.headers["authorization"] == "Bearer test-token"
    assert req.url.params["query"] == QUERIES[0].text
    assert req.url.params["sort_order"] == "recency"  # recent posts, not popular ones

    assert [p.post_id for p in posts] == ["1900000000000000001", "1900000000000000003", "1900000000000000004"]
    first = posts[0]
    assert first.url == "https://x.com/souten_keys/status/1900000000000000001"
    assert first.author_handle == "souten_keys" and first.author_name == "蒼天工房"
    # expanded URL comes from the API entities, not from guessing at t.co
    assert first.links[0].expanded_url == "https://souten-keys.booth.pm/items/555"
    assert first.links[0].source == "api_entities"
    assert first.media_urls == ["https://pbs.twimg.com/media/sample.jpg"]
    # links back to X (photo attachments) are not store links
    assert posts[2].links == []
    assert not first.is_fixture and st.retrieved_at


async def test_usage_cost_is_recorded_against_the_budget(make_kai):
    k, _ = make_kai(live_handler(), x_bearer_token="t", x_max_queries_per_refresh=1)
    _, st = await k.official.search(QUERIES)
    # 4 posts * $0.005 + 3 users * $0.010
    assert st.cost_usd_estimate == pytest.approx(0.05)
    assert k.official.spent() == pytest.approx(0.05)
    usage = k.store.usage_summary()
    assert any(u["service"] == "x_api" and u["operation"] == "search_recent" for u in usage)


@pytest.mark.parametrize("status,state", [(401, "unauthorized"), (403, "forbidden"), (429, "rate_limited")])
async def test_auth_and_rate_limit_failures_are_real_failures_and_not_retried(make_kai, status, state):
    k, net = make_kai(live_handler(x_status=status), x_bearer_token="bad")
    posts, st = await k.official.search(QUERIES)
    assert posts == []
    assert st.state == state and st.http_status == status
    assert str(status) in st.error
    assert len(net.to("api.x.com")) == 1, "must stop after the first refusal: no retry, no next query"
    events = k.store.events(None, 50)
    assert any(e["status"] == "error" and e["stage"] == "x_search" and "Not retried" in e["message"] for e in events)


async def test_spend_ceiling_blocks_requests_before_they_are_made(make_kai):
    k, net = make_kai(live_handler(), x_bearer_token="t", x_max_spend_usd=0.10)
    posts, st = await k.official.search(QUERIES)  # worst case 10 * (0.005 + 0.010) = $0.15 > $0.10
    assert posts == [] and st.state == "budget_exhausted"
    assert "spend ceiling" in st.error
    assert net.requests == []


async def test_spend_ceiling_stops_after_budget_is_used(make_kai):
    k, net = make_kai(live_handler(), x_bearer_token="t", x_max_spend_usd=0.18, x_max_queries_per_refresh=4)
    posts, st = await k.official.search(QUERIES)
    # first query spends $0.05; the next could cost $0.15 -> 0.20 > 0.18, so it is not sent
    assert len(net.to("api.x.com")) == 1
    assert posts and st.state == "ok" and "spend ceiling" in st.error


async def test_results_are_cached_per_query(make_kai):
    k, net = make_kai(live_handler(), x_bearer_token="t", x_max_queries_per_refresh=1)
    first, _ = await k.official.search(QUERIES)
    second, st = await k.official.search(QUERIES)
    assert len(net.to("api.x.com")) == 1
    assert st.from_cache and all(p.from_cache for p in second)
    assert [p.post_id for p in second] == [p.post_id for p in first]
    assert k.official.spent() == pytest.approx(0.05), "cache hits cost nothing"


async def test_invalid_queries_are_never_sent(make_kai):
    k, net = make_kai(live_handler(), x_bearer_token="t")
    bad = XQuery(text="lang:ja", category="keycaps", intent="release", source="category", rationale="x",
                 valid=False, errors=["no standalone term"])
    posts, st = await k.official.search([bad])
    assert posts == [] and net.requests == []


async def test_indexed_provider_uses_search_api_and_marks_snippets_partial(make_kai):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.search.brave.com"
        assert request.headers["x-subscription-token"] == "brave-key"
        assert request.url.params["q"].startswith("site:x.com")
        return httpx.Response(200, json={"web": {"results": [
            {"url": "https://x.com/kumo_resin/status/1900000000000000099", "title": "雲レジン on X: \"【新作】キーキャップ「雲海」販売開始\" / X",
             "description": "【新作】キーキャップ「雲海」販売開始しました 8,000円 https://kumo.booth.pm/items/42", "page_age": "2026-11-18T00:00:00"},
            {"url": "https://example.com/blog", "title": "not a post", "description": "x"},
        ]}})

    k, net = make_kai(handler, brave_api_key="brave-key", x_max_queries_per_refresh=1)
    posts, st = await k.discovery.indexed.search(QUERIES)
    assert st.state == "ok" and len(posts) == 1
    p = posts[0]
    assert p.partial_text and p.author_handle == "kumo_resin" and p.post_id == "1900000000000000099"
    assert p.links[0].url == "https://kumo.booth.pm/items/42"
    assert all(r.url.host != "x.com" for r in net.requests), "x.com itself is never fetched"


async def test_indexed_provider_429_is_a_failure(make_kai):
    k, net = make_kai(lambda r: httpx.Response(429), brave_api_key="k")
    posts, st = await k.discovery.indexed.search(QUERIES)
    assert posts == [] and st.state == "rate_limited" and len(net.requests) == 1


async def test_manual_import_requires_text_without_a_token(make_kai):
    k, net = make_kai()
    post, err = await k.discovery.manual.import_post("https://x.com/maker/status/1234567890")
    assert post is None and "Paste the post text" in err
    post, err = await k.discovery.manual.import_post("https://example.com/not-a-post", "text")
    assert post is None and "not an x.com" in err
    assert net.requests == []


async def test_manual_import_keeps_user_text_and_links(make_kai):
    k, _ = make_kai()
    post, err = await k.discovery.manual.import_post(
        "https://twitter.com/maker/status/1234567890?s=20",
        "【新作】キーキャップ「桜」販売開始 https://maker.booth.pm/items/9", links=["https://maker.base.shop/items/1"])
    assert err is None
    assert post.url == "https://x.com/maker/status/1234567890" and post.author_handle == "maker"
    assert {l.url for l in post.links} == {"https://maker.base.shop/items/1", "https://maker.booth.pm/items/9"}
    assert post.provider.value == "MANUAL_IMPORT" and not post.is_fixture


async def test_manual_import_resolves_by_url_through_official_api_when_authorized(make_kai):
    body = {"data": [X_SEARCH_BODY["data"][0]], "includes": X_SEARCH_BODY["includes"]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/2/tweets" and request.url.params["ids"] == "1900000000000000001"
        return httpx.Response(200, json=body)

    k, net = make_kai(handler, x_bearer_token="t")
    post, err = await k.discovery.manual.import_post("https://x.com/souten_keys/status/1900000000000000001")
    assert err is None and "蒼天" in post.text
    assert post.provider.value == "MANUAL_IMPORT" and "official X API" in post.coverage_note
    assert len(net.requests) == 1


async def test_fixture_provider_is_labeled_and_never_points_at_real_x(make_kai):
    k, net = make_kai()
    posts, st = await k.discovery.fixture.search(QUERIES, category="keycaps")
    assert st.is_fixture and st.state == "ok" and len(posts) >= 3
    for p in posts:
        assert p.is_fixture and p.provider.value == "FIXTURE"
        assert p.url.startswith("https://x.kai-fixture.invalid/")
        assert "SAMPLE DATA" in p.coverage_note
        assert p.query is None, "fixtures are not the result of a search query"
        assert all(".kai-fixture.invalid" in l.url for l in p.links)
    assert net.requests == []


async def test_fixtures_can_be_disabled(make_kai):
    k, _ = make_kai(allow_fixtures=False)
    posts, st = await k.discovery.fixture.search(QUERIES, category="keycaps")
    assert posts == [] and st.state == "unconfigured"


def test_parse_status_url():
    assert parse_status_url("https://x.com/abc_1/status/123?s=20") == ("abc_1", "123")
    assert parse_status_url("https://mobile.twitter.com/abc/statuses/9") == ("abc", "9")
    assert parse_status_url("https://x.com/abc") is None
    assert parse_status_url("https://evil.example/x.com/abc/status/1") is None
