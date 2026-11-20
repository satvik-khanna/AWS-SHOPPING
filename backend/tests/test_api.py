import httpx
import pytest
from fastapi.testclient import TestClient

from kai.app import create_app

from .conftest import REQUIRED_CASE, html_product, html_response

FX = "kai-fixture.invalid"


@pytest.fixture
def client(make_kai):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "maker.booth.pm" and request.url.path == "/items/9":
            return html_response(html_product("桜 キーキャップ", 5500, store="さくら工房"))
        return httpx.Response(404)

    k, net = make_kai(handler)
    with TestClient(create_app(kai=k)) as c:
        c.kai, c.net = k, net
        yield c


def test_ui_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Social Drops" in r.text and "Watched Creators" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/img/fixture-keycap.svg").status_code == 200


def test_status_reports_fallbacks_honestly(client):
    s = client.get("/api/status").json()
    modes = {i["name"]: i["mode"] for i in s["integrations"]}
    assert modes == {"superlinked_sie": "fallback", "cognee": "fallback", "senso": "fallback", "llm": "fallback"}
    assert all(i["detail"] for i in s["integrations"])
    providers = {p["provider"]: p["configured"] for p in s["xProviders"]}
    assert providers == {"X_OFFICIAL_API": False, "INDEXED_WEB_SEARCH": False, "MANUAL_IMPORT": True, "FIXTURE": True}
    assert s["bindingActionsSupported"] == [] and s["xSpend"]["spentUsd"] == 0
    checked = client.post("/api/integrations/check").json()
    assert {i["mode"] for i in checked["integrations"]} == {"fallback"}
    assert client.post("/api/integrations/senso/seed").json()["ok"] is False


def test_agent_run_draft_and_review_flow(client):
    client.post("/api/memory/preferences", json={"text": "I prefer handmade matte pieces, not glossy"})
    run = client.post("/api/agent/run", json={"message": REQUIRED_CASE}).json()
    assert run["draft"]["status"] == "PENDING_REVIEW" and run["stoppedBeforeBindingAction"] is True
    assert run["discovery"]["freshDiscovery"] is False
    assert client.get(f"/api/runs/{run['id']}").json()["id"] == run["id"]
    assert client.get("/api/runs").json()[0]["id"] == run["id"]

    drafts = client.get("/api/drafts").json()
    assert len(drafts) == 1 and drafts[0]["binding"] is False
    did = drafts[0]["id"]
    assert client.post(f"/api/drafts/{did}/review", json={"decision": "purchase"}).status_code == 409
    ok = client.post(f"/api/drafts/{did}/review", json={"decision": "approve", "note": "ok"}).json()
    assert ok["status"] == "APPROVED_FOR_MANUAL_HANDOFF" and ok["binding"] is False
    assert client.post(f"/api/drafts/{did}/review", json={"decision": "approve"}).status_code == 409
    assert client.post("/api/drafts/nope/review", json={"decision": "approve"}).status_code == 404
    assert client.net.requests == []

    timeline = client.get(f"/api/activity?runId={run['id']}").json()
    assert {"x_queries", "x_provider", "storefront", "ranking", "draft", "stop"} <= {e["stage"] for e in timeline}


def test_drafts_from_ranked_candidates_respect_purchase_paths(client):
    run = client.post("/api/agent/run", json={"message": REQUIRED_CASE}).json()
    by_avail = {r["candidate"]["availability"]: r["candidate"]["id"] for r in run["ranked"]}
    sold = client.post("/api/drafts", json={"runId": run["id"], "candidateId": by_avail["SOLD_OUT"]})
    assert sold.status_code == 409 and "sold out" in sold.json()["detail"].lower()
    lottery = client.post("/api/drafts", json={"runId": run["id"], "candidateId": by_avail["LOTTERY"]}).json()
    assert lottery["path"] == "MANUAL_ONLY" and lottery["status"] == "PENDING_REVIEW"
    assert client.post("/api/drafts", json={"runId": run["id"], "candidateId": "missing"}).status_code == 404
    assert client.post("/api/drafts", json={}).status_code == 400


def test_social_refresh_feed_and_shortlist(client):
    assert client.get("/api/social/leads").json()["leads"] == []
    r = client.post("/api/social/refresh", json={"category": "keycaps"}).json()
    assert r["lastRefresh"]["fresh"] is False and r["lastRefresh"]["queries"]
    feed = client.get("/api/social/leads").json()
    leads = feed["leads"]
    assert len(leads) >= 3 and feed["lastRefresh"]["at"]
    for lead in leads:
        for key in ["id", "sourcePostUrl", "postId", "authorHandle", "authorProfileUrl", "originalJapaneseText",
                    "englishTranslation", "productName", "productCategory", "announcementType", "announcedPriceJPY",
                    "releaseDate", "preorderDeadline", "lotteryDeadline", "externalLinks", "mediaUrls", "confidence",
                    "sourceTimestamp", "retrievalTimestamp", "availabilityStatus", "provenance", "verificationStatus"]:
            assert key in lead, key
        assert lead["provenance"]["isFixture"] is True
    assert [l["sourceTimestamp"] for l in leads] == sorted((l["sourceTimestamp"] for l in leads), reverse=True)

    target = next(l for l in leads if l["verificationStatus"] == "LISTING_RESOLVED" and l["availabilityStatus"] == "IN_STOCK")
    item = client.post("/api/shortlist", json={"leadId": target["id"]}).json()
    assert item["candidate"]["purchaseUrl"] == target["storefront"]["purchaseUrl"]
    assert client.post("/api/shortlist", json={"leadId": target["id"]}).json()["id"] == item["id"], "no duplicates"
    assert len(client.get("/api/shortlist").json()) == 1
    # the flag survives a refresh
    client.post("/api/social/refresh", json={"category": "keycaps"})
    again = next(l for l in client.get("/api/social/leads").json()["leads"] if l["id"] == target["id"])
    assert again["shortlisted"] is True

    draft = client.post("/api/drafts", json={"shortlistId": item["id"]}).json()
    assert draft["status"] == "PENDING_REVIEW" and draft["binding"] is False and draft["cost"]["totalUsd"]
    assert client.delete(f"/api/shortlist/{item['id']}").json()["deleted"] is True
    assert client.post("/api/shortlist", json={"leadId": "nope"}).status_code == 404
    assert client.post("/api/refresh-unknown", json={}).status_code == 404
    assert client.post("/api/social/refresh", json={"category": "spaceships"}).status_code == 400


def test_refresh_other_categories(client):
    for cat in ["figures", "handmade", "cameras"]:
        r = client.post("/api/social/refresh", json={"category": cat}).json()
        assert r["results"][0]["category"] == cat and r["results"][0]["leads"], cat


def test_watched_creators_crud_and_use_in_queries(client):
    assert client.post("/api/watched", json={"handle": "not a handle!"}).status_code == 400
    assert client.post("/api/watched", json={}).status_code == 400
    w = client.post("/api/watched", json={"handle": "@tsukumo_keys", "categories": ["keycaps", "bogus"],
                                          "searchTerms": ["レジンキーキャップ"]}).json()
    assert w["handle"] == "tsukumo_keys" and w["categories"] == ["keycaps"]
    assert len(client.get("/api/watched").json()) == 1
    r = client.post("/api/social/refresh", json={}).json()
    assert r["lastRefresh"]["categories"] == ["keycaps"]
    texts = [q["text"] for q in r["lastRefresh"]["queries"]]
    assert any("from:tsukumo_keys" in t for t in texts)
    assert client.delete(f"/api/watched/{w['id']}").json()["deleted"] is True


def test_manual_post_import_traces_the_store(client):
    bad = client.post("/api/social/import", json={"url": "https://example.com/x"})
    assert bad.status_code == 400
    no_text = client.post("/api/social/import", json={"url": "https://x.com/maker/status/1234567890123"})
    assert no_text.status_code == 400 and "Paste the post text" in no_text.json()["detail"]

    lead = client.post("/api/social/import", json={
        "url": "https://x.com/sakura_maker/status/1234567890123",
        "text": "【新作】キーキャップ「桜」販売開始しました！ 5,500円 https://maker.booth.pm/items/9"}).json()
    assert lead["provenance"]["provider"] == "MANUAL_IMPORT" and lead["provenance"]["isFixture"] is False
    assert lead["verificationStatus"] == "LISTING_RESOLVED"
    assert lead["storefront"]["purchaseUrl"] == "https://maker.booth.pm/items/9"
    assert lead["sourcePostUrl"] == "https://x.com/sakura_maker/status/1234567890123"
    assert lead["announcedPriceJPY"] == 5500 and lead["storefront"]["priceJpy"] == 5500
    assert all(r.url.host == "maker.booth.pm" for r in client.net.requests)

    # the imported post takes part in the next refresh alongside fixtures
    r = client.post("/api/social/refresh", json={"category": "keycaps"}).json()
    providers = {s["provider"]: s for s in r["lastRefresh"]["providerStatuses"]}
    assert providers["MANUAL_IMPORT"]["state"] == "ok" and r["lastRefresh"]["fresh"] is False
    assert client.delete(f"/api/social/leads/{lead['id']}").json()["deleted"] is True


def test_listing_import(client):
    c = client.post("/api/listings/import", json={"url": "https://maker.booth.pm/items/9"}).json()
    assert c["source"] == "URL_IMPORT" and c["priceJpy"] == 5500 and c["isFixture"] is False
    assert c["purchaseUrl"] == "https://maker.booth.pm/items/9" and c["availability"] == "IN_STOCK"
    assert len(client.get("/api/listings").json()) == 1
    assert client.post("/api/listings/import", json={"url": "https://maker.booth.pm/items/404"}).status_code == 422
    assert client.post("/api/listings/import", json={"url": "http://127.0.0.1/items/1"}).status_code == 422
    assert client.post("/api/listings/import", json={"url": "file:///etc/passwd"}).status_code == 422

    run = client.post("/api/agent/run", json={"message": REQUIRED_CASE}).json()
    imported = [r for r in run["ranked"] if r["candidate"]["source"] == "URL_IMPORT"]
    assert imported and imported[0]["cost"]["totalUsd"], "imported listings join the comparison"
    assert client.delete(f"/api/listings/{c['id']}").json()["deleted"] is True


def test_memory_endpoints(client):
    saved = client.post("/api/memory/preferences", json={"text": "I prefer handmade things, no glossy finishes"}).json()
    assert {p["attribute"] for p in saved["saved"]} == {"handmade", "glossy"}
    assert saved["backend"]["mode"] == "fallback"
    mem = client.get("/api/memory").json()
    assert len(mem["preferences"]) == 2
    pid = mem["preferences"][0]["id"]
    assert client.delete(f"/api/memory/preferences/{pid}").json()["deleted"] is True
    assert len(client.get("/api/memory").json()["preferences"]) == 1
    assert client.post("/api/memory/preferences", json={"text": ""}).status_code == 422


def test_bad_requests(client):
    assert client.post("/api/agent/run", json={"message": ""}).status_code == 422
    r = client.post("/api/agent/run", json={"message": "what's the weather"}).json()
    assert r["error"] == "unrecognized_request"
    assert client.get("/api/runs/nope").status_code == 404


def test_no_route_can_submit_an_order(client):
    paths = {route.path for route in client.app.routes}
    for word in ("order", "checkout", "pay", "bid", "submit", "purchase"):
        assert not any(word in p.lower() for p in paths), f"unexpected route containing '{word}'"
