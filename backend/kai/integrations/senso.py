"""Senso adapter: retrieval of cited passages for landed-cost assumptions.

Uses the documented API (base https://apiv2.senso.ai/api/v1, `X-API-Key`):
  GET  /org/me               - key check
  POST /org/kb/raw           - add a source document (202; 409 if identical text exists)
  POST /org/search/context   - passages only, each with content_id/title/chunk_text/score

Seeding writes to the user's Senso knowledge base and uses credits, so it only
happens on explicit request (POST /api/integrations/senso/seed).

Fallback: the same source documents bundled in kai/data/cost_sources.json,
matched locally. Citations carry `via` = "senso" or "bundled" accordingly."""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import PKG, Settings
from ..models import Citation
from ..observability import ActivityLog
from ..store import Store
from ..textutil import ngram_containment
from . import IntegrationState


def load_cost_sources() -> list[dict[str, Any]]:
    return json.loads((PKG / "data" / "cost_sources.json").read_text(encoding="utf-8"))["sources"]


class SensoAdapter:
    def __init__(self, settings: Settings, store: Store, log: ActivityLog,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.store = store
        self.log = log
        self._transport = transport
        self.sources = load_cost_sources()
        self._by_id = {s["id"]: s for s in self.sources}
        self.state = IntegrationState(
            name="senso", label="Senso (cited cost sources)", configured=bool(settings.senso_api_key),
            fallback_label="bundled source documents (local match)",
            unconfigured_detail="SENSO_API_KEY not set; cost citations come from the bundled source documents "
                                "(real URLs, retrieved on the date shown), matched locally.",
        )

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.settings.senso_api_key or "", "Content-Type": "application/json"}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(20.0, connect=5.0))

    def _bundled_citation(self, source_id: str, excerpt: str | None = None) -> Citation:
        s = self._by_id[source_id]
        return Citation(id=s["id"], title=s["title"], url=s.get("url"), publisher=s.get("publisher"),
                        retrieved_at=s.get("retrievedAt"), excerpt=excerpt or s.get("excerpt"), via="bundled")

    async def whoami(self) -> dict[str, Any]:
        if not self.state.configured:
            return self.state.to_json()
        try:
            async with self._client() as c:
                r = await c.get(f"{self.settings.senso_base_url}/org/me", headers=self._headers())
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            org = r.json()
            self.state.ok()
            self.state.extra["org"] = org.get("name") or org.get("slug")
        except Exception as e:  # noqa: BLE001
            self.state.fail(f"{type(e).__name__}: {e}")
        return self.state.to_json()

    async def seed(self) -> dict[str, Any]:
        """Upload the bundled cost sources to the Senso knowledge base (explicit action)."""
        if not self.state.configured:
            return {"ok": False, "error": "SENSO_API_KEY not configured", "results": []}
        results = []
        async with self._client() as c:
            for s in self.sources:
                text = (f"{s['title']}\nPublisher: {s.get('publisher')}\nSource URL: {s.get('url')}\n"
                        f"Retrieved: {s.get('retrievedAt')}\nKAI source id: {s['id']}\n\n{s['text']}")
                try:
                    r = await c.post(f"{self.settings.senso_base_url}/org/kb/raw", headers=self._headers(),
                                     json={"title": s["title"], "text": text})
                    if r.status_code in (200, 201, 202):
                        body = r.json()
                        self.store.put("senso_seed", s["id"], {"sourceId": s["id"], "contentId": body.get("id"),
                                                               "kbNodeId": body.get("kb_node_id")})
                        self.state.ok()
                        results.append({"sourceId": s["id"], "status": "accepted", "contentId": body.get("id")})
                    elif r.status_code == 409:
                        results.append({"sourceId": s["id"], "status": "already_present"})
                    else:
                        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                except Exception as e:  # noqa: BLE001
                    self.state.fail(f"{type(e).__name__}: {e}")
                    results.append({"sourceId": s["id"], "status": "error", "error": self.state.last_error})
        ok = all(x["status"] != "error" for x in results)
        self.log.log("senso", f"Senso seed: {sum(1 for x in results if x['status'] == 'accepted')} accepted, "
                              f"{sum(1 for x in results if x['status'] == 'already_present')} already present, "
                              f"{sum(1 for x in results if x['status'] == 'error')} failed", "ok" if ok else "error")
        return {"ok": ok, "results": results}

    async def cite(self, question: str, source_ids: list[str], run_id: str | None = None) -> tuple[list[Citation], str]:
        """Return citations for a cost component and the backend that produced them.

        `source_ids` are the bundled documents the numeric parameter was taken
        from. With Senso live, passages are retrieved from Senso and mapped back
        to those documents; passages Senso returns from other documents are
        ignored for this component rather than presented as support."""
        if self.state.configured:
            try:
                async with self._client() as c:
                    r = await c.post(f"{self.settings.senso_base_url}/org/search/context", headers=self._headers(),
                                     json={"query": question, "max_results": 5})
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                self.state.ok()
                self.log.usage("senso", "search_context", 1, "queries", None, {"question": question[:80]})
                seeded = {d.get("contentId"): d["sourceId"] for d in self.store.list("senso_seed") if d.get("contentId")}
                by_title = {s["title"]: s["id"] for s in self.sources}
                out: list[Citation] = []
                seen: set[str] = set()
                for hit in r.json().get("results") or []:
                    sid = seeded.get(hit.get("content_id")) or by_title.get(hit.get("title") or "")
                    if not sid or sid not in source_ids or sid in seen:
                        continue
                    seen.add(sid)
                    s = self._by_id[sid]
                    out.append(Citation(id=sid, title=s["title"], url=s.get("url"), publisher=s.get("publisher"),
                                        retrieved_at=s.get("retrievedAt"),
                                        excerpt=(hit.get("chunk_text") or "")[:400] or s.get("excerpt"),
                                        via="senso", score=hit.get("score"), senso_content_id=hit.get("content_id")))
                if out:
                    return out, "senso"
                self.log.log("cost", f"Senso returned no passage from the expected sources for: {question[:70]}; "
                                     "citing bundled copies instead.", "warn", run_id)
            except Exception as e:  # noqa: BLE001
                self.state.fail(f"{type(e).__name__}: {e}")
                self.log.log("cost", f"Senso search failed; citing bundled sources. {self.state.last_error}", "warn", run_id)
        else:
            self.state.used_fallback()
        return [self._bundled_citation(sid, self._best_excerpt(sid, question)) for sid in source_ids if sid in self._by_id], "bundled"

    def _best_excerpt(self, source_id: str, question: str) -> str | None:
        s = self._by_id[source_id]
        paras = [p.strip() for p in s["text"].split("\n") if p.strip()]
        if not paras:
            return s.get("excerpt")
        best = max(paras, key=lambda p: ngram_containment(question, p))
        return best[:400]
