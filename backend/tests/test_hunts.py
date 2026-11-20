import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from kai.app import create_app
from kai.hunts import HuntService, product_key
from kai.models import Candidate, RankedCandidate, ScoreBreakdown
from kai.store import Store

from .conftest import REQUIRED_CASE, html_product, html_response


URL = "https://maker.booth.pm/items/9"
BRIEF = "Marketplace only artisan keycaps under $160 delivered to California"


def expire_page(k, url=URL):
    k.store.cache_set("fetch:" + url, {}, ttl_s=-1)


async def test_hunt_observes_restock_price_drop_and_does_not_repeat_or_create_drafts(make_kai, tmp_path):
    page = {"price": 9000, "availability": "OutOfStock", "status": 200}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return html_response(html_product("桜 キーキャップ", page["price"], page["availability"]), page["status"])

    k, _ = make_kai(handler, allow_fixtures=False)
    await k.marketplace.import_url(URL)
    run = await k.agent.run(BRIEF)
    hunt = k.hunts.create(run.id)
    assert k.hunts.create(run.id)["id"] == hunt["id"], "saving a brief twice is idempotent"

    # A fresh service/database connection recovers the baseline.
    reopened = Store(tmp_path / "kai0.sqlite3")
    assert HuntService(reopened, k.agent).list()[0]["id"] == hunt["id"]
    reopened._db.close()

    page.update(price=6000, availability="InStock")
    expire_page(k)
    result = await k.hunts.check(hunt["id"])
    assert len(result["check"]["changes"]) == 1
    change = result["check"]["changes"][0]
    assert {"RESTOCK", "PRICE_DROP", "NOW_MATCHES_BRIEF"} <= set(change["kinds"])
    assert change["before"]["priceJpy"] == 9000 and change["after"]["priceJpy"] == 6000
    assert result["check"]["matchCount"] == 1 and result["check"]["sampleCount"] == 0
    assert result["run"]["draft"] is None and k.purchase.list() == []
    assert len(k.store.list("imported_listing")) == 1, "refresh updates rather than duplicating the import"
    assert result["run"]["timeline"][-1]["stage"] == "hunt"
    assert (await k.hunts.check(hunt["id"]))["check"]["changes"] == []

    # Failed refreshes do not turn old stock into a new observation or erase it.
    page["status"] = 503
    expire_page(k)
    failed = await k.hunts.check(hunt["id"])
    assert failed["check"]["candidateCount"] == 0 and failed["check"]["changes"] == []
    assert failed["run"]["marketplaceSources"][1]["state"] == "error"
    page["status"] = 200
    recovered = await k.hunts.check(hunt["id"])
    assert recovered["check"]["changes"] == [], "recovery must not repeat previously observed changes"


async def test_new_match_is_reported_once_and_current_memory_changes_fit(make_kai):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return html_response(html_product("桜 キーキャップ", 6000))

    k, _ = make_kai(handler, allow_fixtures=False)
    run = await k.agent.run(BRIEF)
    hunt = k.hunts.create(run.id)
    await k.marketplace.import_url(URL)
    first = await k.hunts.check(hunt["id"])
    assert first["check"]["changes"][0]["kinds"] == ["NEW_MATCH"]
    assert (await k.hunts.check(hunt["id"]))["check"]["changes"] == []
    await k.memory.add_preference("I avoid matte finishes")
    assert (await k.hunts.check(hunt["id"]))["check"]["matchCount"] == 0
    await k.memory.add_preference("I prefer matte finishes")
    restored = await k.hunts.check(hunt["id"])
    assert restored["check"]["changes"][0]["kinds"] == ["NOW_MATCHES_BRIEF"]
    assert k.purchase.list() == []


async def test_overlapping_checks_and_deletion_are_rejected(make_kai):
    k, _ = make_kai()
    run = await k.agent.run(REQUIRED_CASE)
    hunt = k.hunts.create(run.id)
    started, finish = asyncio.Event(), asyncio.Event()
    original = k.agent.run

    async def waiting_run(*args, **kwargs):
        started.set()
        await finish.wait()
        return await original(*args, **kwargs)

    k.agent.run = waiting_run
    task = asyncio.create_task(k.hunts.check(hunt["id"]))
    await started.wait()
    try:
        with pytest.raises(ValueError, match="already being checked"):
            await k.hunts.check(hunt["id"])
        with pytest.raises(ValueError, match="being checked"):
            k.hunts.delete(hunt["id"])
    finally:
        finish.set()
        await task


async def test_rechecking_an_old_brief_does_not_relearn_outdated_lasting_preferences(make_kai):
    k, _ = make_kai()
    run = await k.agent.run("I prefer matte. Find artisan keycaps under $150 to California")
    hunt = k.hunts.create(run.id)
    await k.memory.add_preference("I avoid matte")
    await k.hunts.check(hunt["id"])
    memory = await k.memory.recall("keycaps")
    assert "matte" in memory.avoided and "matte" not in memory.preferred


def test_product_identity_preserves_variants_and_separates_samples():
    def rc(url, sample=False):
        return RankedCandidate(candidate=Candidate(id="changing-id", source="URL_IMPORT", source_label="test",
                               title="Keycap", purchase_url=url, is_fixture=sample), score=ScoreBreakdown())

    a = rc(URL + "?variant=1&utm_source=x")
    assert product_key(a) == product_key(rc(URL + "?variant=1"))
    assert product_key(a) != product_key(rc(URL + "?variant=2"))
    assert product_key(a) != product_key(rc(URL + "?variant=1", sample=True))


def test_hunt_api_save_check_delete_and_error_states(make_kai):
    k, _ = make_kai()
    with TestClient(create_app(kai=k)) as c:
        assert c.get("/api/hunts").json() == []
        assert c.post("/api/hunts", json={"runId": "missing"}).status_code == 404
        run = c.post("/api/agent/run", json={"message": REQUIRED_CASE}).json()
        hunt = c.post("/api/hunts", json={"runId": run["id"], "title": "My keycaps"}).json()
        assert hunt["title"] == "My keycaps" and "snapshots" not in hunt
        check = c.post(f"/api/hunts/{hunt['id']}/check").json()
        assert check["check"]["changes"] == [] and check["check"]["sampleCount"] > 0
        assert check["check"]["freshXDiscovery"] is False
        assert len(c.get("/api/drafts").json()) == 1, "the recheck must not duplicate the initial draft"
        assert c.post("/api/hunts/missing/check").status_code == 404
        assert c.delete(f"/api/hunts/{hunt['id']}").json() == {"deleted": True}
        assert c.get("/api/hunts").json() == []
