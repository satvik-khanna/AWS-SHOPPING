"""Adapter contract tests. These verify request shape and fallback behaviour
against simulated servers; they do not prove the live services work."""

import json

import httpx

from kai.integrations.sie import FALLBACK_BACKEND
from kai.models import Candidate, MemoryRecall, ParsedRequest

# ---------------------------------------------------------------- Superlinked SIE


async def test_sie_unconfigured_uses_labeled_lexical_fallback(make_kai):
    k, net = make_kai()
    vecs, backend = await k.sie.embed(["キーキャップ", "カメラ"])
    assert backend == FALLBACK_BACKEND and "not semantic" in backend
    assert len(vecs) == 2 and len(vecs[0]) == 512
    scores, label = await k.sie.rerank("q", [("a", "x")])
    assert scores is None and label == "none"
    st = k.sie.state.to_json()
    assert st["mode"] == "fallback" and st["configured"] is False and st["liveCalls"] == 0
    assert net.requests == []


async def test_sie_encode_and_score_request_contract(make_kai):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer sie-key"
        assert request.headers["accept"] == "application/json"
        if request.url.raw_path.startswith(b"/v1/encode/"):
            assert request.url.raw_path == b"/v1/encode/BAAI%2Fbge-m3"
            assert body["params"] == {"output_types": ["dense"]}
            return httpx.Response(200, json={"model": "BAAI/bge-m3", "items": [
                {"id": it["id"], "dense": {"dims": 3, "dtype": "float32", "values": [0.1, 0.2, float(i)]}}
                for i, it in enumerate(body["items"])]})
        assert request.url.raw_path == b"/v1/score/BAAI%2Fbge-reranker-v2-m3"
        assert body["query"] == {"text": "matte keycap"}
        return httpx.Response(200, json={"model": "x", "scores": [
            {"item_id": "a", "score": 2.0, "rank": 0}, {"item_id": "b", "score": -1.0, "rank": 1}]})

    k, net = make_kai(handler, sie_api_key="sie-key")
    assert k.sie.base_url == "https://api.superlinked.com"
    vecs, backend = await k.sie.embed(["one", "two"])
    assert backend == "sie:BAAI/bge-m3" and vecs == [[0.1, 0.2, 0.0], [0.1, 0.2, 1.0]]
    scores, label = await k.sie.rerank("matte keycap", [("a", "matte"), ("b", "glossy")])
    assert scores == {"a": 1.0, "b": 0.0} and label == "sie:BAAI/bge-reranker-v2-m3"
    st = k.sie.state.to_json()
    assert st["mode"] == "live" and st["liveCalls"] == 2
    assert all(r.url.host == "api.superlinked.com" for r in net.requests)


async def test_sie_self_hosted_raw_slash_retry_on_404(make_kai):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path)
        if b"%2F" in request.url.raw_path:
            return httpx.Response(404, json={"detail": "Not Found"})
        body = json.loads(request.content)
        return httpx.Response(200, json={"items": [{"id": it["id"], "dense": {"values": [1.0, 0.0]}} for it in body["items"]]})

    k, _ = make_kai(handler, sie_base_url="http://localhost:8080")
    vecs, backend = await k.sie.embed(["x"])
    assert backend.startswith("sie:") and seen == [b"/v1/encode/BAAI%2Fbge-m3", b"/v1/encode/BAAI/bge-m3"]


async def test_sie_failure_falls_back_and_reports_error(make_kai):
    k, _ = make_kai(lambda r: httpx.Response(401, json={"detail": "bad key"}), sie_api_key="bad")
    vecs, backend = await k.sie.embed(["x"])
    assert backend == FALLBACK_BACKEND and len(vecs[0]) == 512
    st = k.sie.state.to_json()
    assert st["mode"] == "error" and "401" in st["lastError"] and st["liveCalls"] == 0
    health = await k.sie.health()
    assert health["mode"] == "error"


async def test_sie_malformed_response_is_not_trusted(make_kai):
    k, _ = make_kai(lambda r: httpx.Response(200, json={"items": [{"id": "t0"}]}), sie_api_key="k")
    _, backend = await k.sie.embed(["x"])
    assert backend == FALLBACK_BACKEND and k.sie.state.to_json()["mode"] == "error"


def _cand(i, title, attrs, price=5000):
    return Candidate(id=f"c{i}", source="MARKETPLACE", source_label="test", title=title, price_jpy=price,
                     purchase_url=f"https://s.example.jp/items/{i}", availability="IN_STOCK", attributes=attrs,
                     verification="LISTING_RESOLVED")


async def test_ranker_reports_which_backend_scored(make_kai):
    k, _ = make_kai()
    req = ParsedRequest(raw="artisan keycaps", category="keycaps", category_label="Artisan keycaps", ja_terms=["キーキャップ"], budget_usd=120)
    mem = MemoryRecall(backend="fallback", preferred=["handmade", "matte"], avoided=["glossy"])
    cands = [_cand(1, "キーキャップ 光沢仕上げ 量産品", ["glossy", "mass_produced"]),
             _cand(2, "アルチザンキーキャップ ハンドメイド マット", ["handmade", "matte"])]
    costs = {c.id: await k.costs.estimate(c, "keycaps", 120, "California, US", True) for c in cands}
    ranked = await k.agent.ranker.rank(req, mem, cands, costs)
    assert [r.candidate.id for r in ranked] == ["c2", "c1"]
    assert ranked[0].score.ranking_backend == FALLBACK_BACKEND
    assert ranked[0].score.preference > ranked[1].score.preference
    assert any("avoid" in r.lower() for r in ranked[1].score.reasons)
    assert [r.rank for r in ranked] == [1, 2]


# ---------------------------------------------------------------- Senso


async def test_senso_unconfigured_cites_bundled_sources(make_kai):
    k, net = make_kai()
    cites, backend = await k.senso.cite("What duty applies to Japan-origin goods?", ["zonos_us_tariff_tracker", "fedreg_2026_21600"])
    assert backend == "bundled" and [c.id for c in cites] == ["zonos_us_tariff_tracker", "fedreg_2026_21600"]
    assert all(c.via == "bundled" and c.url.startswith("https://") and c.retrieved_at for c in cites)
    assert (await k.senso.seed()) == {"ok": False, "error": "SENSO_API_KEY not configured", "results": []}
    assert k.senso.state.to_json()["mode"] == "fallback"
    assert net.requests == []


async def test_senso_seed_and_search_contract(make_kai):
    uploaded = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "senso-key"
        assert request.url.host == "apiv2.senso.ai"
        path = request.url.path
        if path.endswith("/org/me"):
            return httpx.Response(200, json={"name": "Test Org"})
        if path.endswith("/org/kb/raw"):
            body = json.loads(request.content)
            assert body["title"] and "Source URL:" in body["text"]
            uploaded.append(body["title"])
            return httpx.Response(202, json={"id": f"content-{len(uploaded)}", "kb_node_id": "n", "processing_status": "processing"})
        assert path.endswith("/org/search/context")
        assert json.loads(request.content)["query"]
        return httpx.Response(200, json={"results": [
            {"content_id": "content-999", "title": "Some unrelated doc", "chunk_text": "irrelevant", "score": 0.9},
            {"content_id": "content-4", "title": "ignored", "chunk_text": "Japan-origin goods: 12.5% cap passage", "score": 0.8},
        ]})

    k, _ = make_kai(handler, senso_api_key="senso-key")
    assert (await k.senso.whoami())["org"] == "Test Org"
    seed = await k.senso.seed()
    assert seed["ok"] and len(seed["results"]) == len(k.senso.sources) == len(uploaded)
    fourth = k.senso.sources[3]["id"]
    cites, backend = await k.senso.cite("duty?", [fourth])
    assert backend == "senso" and len(cites) == 1
    c = cites[0]
    assert c.id == fourth and c.via == "senso" and c.senso_content_id == "content-4" and "12.5%" in c.excerpt
    assert k.senso.state.to_json()["mode"] == "live"


async def test_senso_passages_from_unexpected_documents_are_not_presented_as_support(make_kai):
    k, _ = make_kai(lambda r: httpx.Response(200, json={"results": [
        {"content_id": "zzz", "title": "Unrelated", "chunk_text": "duty is 0%", "score": 0.99}]}), senso_api_key="k")
    cites, backend = await k.senso.cite("duty?", ["zonos_us_tariff_tracker"])
    assert backend == "bundled" and cites[0].via == "bundled" and "0%" not in (cites[0].excerpt or "")


async def test_senso_error_falls_back_and_reports(make_kai):
    k, _ = make_kai(lambda r: httpx.Response(401, json={"error": "invalid key"}), senso_api_key="bad")
    cites, backend = await k.senso.cite("duty?", ["zonos_us_tariff_tracker"])
    assert backend == "bundled" and cites
    st = k.senso.state.to_json()
    assert st["mode"] == "error" and "401" in st["lastError"]
    assert (await k.senso.seed())["ok"] is False


# ---------------------------------------------------------------- Cognee


class FakeCognee:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.remembered: list[tuple[str, str]] = []
        self.recalled: list[tuple[str, list[str]]] = []

    async def remember(self, text, dataset_name=None):
        if self.fail:
            raise RuntimeError("LLM provider rejected the key")
        self.remembered.append((text, dataset_name))

    async def recall(self, query, datasets=None, top_k=5):
        if self.fail:
            raise RuntimeError("graph unavailable")
        self.recalled.append((query, datasets))
        return [type("R", (), {"text": "User prefers handmade items with a matte finish"})()]


async def test_cognee_not_installed_uses_local_store_and_says_so(make_kai):
    k, _ = make_kai()
    saved = await k.memory.add_preference("I prefer handmade pieces with a matte finish, not glossy")
    assert {(p["attribute"], p["polarity"]) for p in saved} == {("handmade", "like"), ("matte", "like"), ("glossy", "avoid")}
    assert all(p["inCognee"] is False for p in saved)
    rec = await k.memory.recall("keycaps")
    assert rec.backend == "fallback" and rec.backend_reason
    assert set(rec.preferred) == {"handmade", "matte"} and rec.avoided == ["glossy"]
    assert k.cognee.state.to_json()["mode"] == "fallback"


async def test_cognee_adapter_calls_remember_and_recall(make_kai):
    fake = FakeCognee()
    k, _ = make_kai(cognee_module=fake)
    saved = await k.memory.add_preference("I love matte handmade things")
    assert all(p["inCognee"] for p in saved)
    assert fake.remembered and fake.remembered[0][1] == "kai_user_default"
    rec = await k.memory.recall("artisan keycaps")
    assert rec.backend == "cognee" and rec.cognee_results == ["User prefers handmade items with a matte finish"]
    assert fake.recalled[0][1] == ["kai_user_default"]
    assert {"handmade", "matte"} <= set(rec.preferred)
    assert k.cognee.state.to_json()["mode"] == "live"


async def test_cognee_failure_keeps_local_memory_and_reports_error(make_kai):
    k, _ = make_kai(cognee_module=FakeCognee(fail=True))
    saved = await k.memory.add_preference("I prefer matte finishes")
    assert saved and saved[0]["inCognee"] is False
    rec = await k.memory.recall("keycaps")
    assert rec.backend == "fallback" and rec.preferred == ["matte"]
    st = k.cognee.state.to_json()
    assert st["mode"] == "error" and "rejected the key" in st["lastError"] or "graph unavailable" in st["lastError"]


async def test_cognee_can_be_disabled(make_kai):
    k, _ = make_kai(cognee_module=FakeCognee(), cognee_enabled="false")
    assert await k.cognee.remember("x") is False
    assert k.cognee.state.to_json()["configured"] is False


async def test_cognee_recall_reads_every_entry_shape(make_kai):
    class Shapes(FakeCognee):
        async def recall(self, query, datasets=None, top_k=5):
            return [type("G", (), {"text": "graph text"})(), type("Q", (), {"answer": "qa answer", "text": None})(),
                    {"content": "session content"}, type("E", (), {})()]

    k, _ = make_kai(cognee_module=Shapes())
    assert await k.cognee.recall("anything") == ["graph text", "qa answer", "session content"]
