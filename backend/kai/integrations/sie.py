"""Superlinked SIE adapter (Search Inference Engine).

Uses the documented HTTP contract:
  POST {base}/v1/encode/{model}  {"items":[{"id","text"}], "params":{"output_types":["dense"]}}
  POST {base}/v1/score/{model}   {"query":{"text"}, "items":[{"id","text"}]}
  GET  {base}/v1/models
with `Accept: application/json` and `Authorization: Bearer <key>` when a key
is set (SIE Serverless: https://api.superlinked.com; self-hosted: e.g.
http://localhost:8080).

Fallback: a deterministic hashed character-n-gram vector. It is lexical, not
semantic, and every result carries the backend label so the UI can say so."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx

from ..config import Settings
from ..observability import ActivityLog
from ..textutil import hash_embed
from . import IntegrationState

FALLBACK_BACKEND = "fallback:hashed-char-ngrams (lexical, not semantic)"


class SIEAdapter:
    def __init__(self, settings: Settings, log: ActivityLog, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.log = log
        self._transport = transport
        self.base_url = settings.sie_effective_base_url
        self.state = IntegrationState(
            name="superlinked_sie", label="Superlinked SIE (ranking)", configured=bool(self.base_url),
            fallback_label="local hashed n-gram ranker (lexical, not semantic)",
            unconfigured_detail="SIE_API_KEY / SIE_BASE_URL not set; ranking uses a local hashed n-gram "
                                "similarity (lexical, not semantic) and no cross-encoder rerank.",
            extra={"baseUrl": self.base_url, "embedModel": settings.sie_embed_model,
                   "rerankModel": settings.sie_rerank_model},
        )

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.settings.sie_api_key:
            h["Authorization"] = f"Bearer {self.settings.sie_api_key}"
        return h

    async def _post(self, primitive: str, model: str, body: dict[str, Any]) -> dict[str, Any]:
        assert self.base_url
        # Serverless examples percent-encode the model id; self-hosted examples use raw slashes.
        paths = [f"/v1/{primitive}/{quote(model, safe='')}", f"/v1/{primitive}/{model}"]
        last: httpx.Response | None = None
        async with httpx.AsyncClient(transport=self._transport, timeout=httpx.Timeout(40.0, connect=5.0)) as client:
            for path in paths:
                for attempt in range(3):
                    r = await client.post(self.base_url + path, headers=self._headers(), json=body)
                    last = r
                    # 503 + Retry-After means a model is loading/provisioning: wait briefly, bounded.
                    if r.status_code == 503 and attempt < 2:
                        try:
                            delay = min(float(r.headers.get("retry-after", "2")), 5.0)
                        except ValueError:
                            delay = 2.0
                        await asyncio.sleep(delay)
                        continue
                    break
                if last is not None and last.status_code != 404:
                    break
        assert last is not None
        if last.status_code >= 400:
            raise RuntimeError(f"SIE {primitive} HTTP {last.status_code}: {last.text[:200]}")
        return last.json()

    async def embed(self, texts: list[str], is_query: bool = False, run_id: str | None = None) -> tuple[list[list[float]], str]:
        """Return (vectors, backend_label)."""
        if not texts:
            return [], FALLBACK_BACKEND
        if self.base_url:
            try:
                body: dict[str, Any] = {
                    "items": [{"id": f"t{i}", "text": t[:2000]} for i, t in enumerate(texts)],
                    "params": {"output_types": ["dense"]},
                }
                data = await self._post("encode", self.settings.sie_embed_model, body)
                items = data.get("items") or []
                vecs = [list((it.get("dense") or {}).get("values") or []) for it in items]
                if len(vecs) != len(texts) or any(not v for v in vecs):
                    raise RuntimeError("SIE encode returned an unexpected shape")
                self.state.ok()
                self.log.usage("superlinked_sie", "encode", len(texts), "items", None,
                               {"model": self.settings.sie_embed_model})
                return vecs, f"sie:{self.settings.sie_embed_model}"
            except Exception as e:  # noqa: BLE001
                self.state.fail(f"{type(e).__name__}: {e}")
                self.log.log("ranking", f"Superlinked SIE encode failed; using local fallback. {self.state.last_error}",
                             "warn", run_id)
        else:
            self.state.used_fallback()
        return [hash_embed(t) for t in texts], FALLBACK_BACKEND

    async def rerank(self, query: str, docs: list[tuple[str, str]], run_id: str | None = None) -> tuple[dict[str, float] | None, str]:
        """Cross-encoder rerank. Returns ({id: score normalised to 0..1} or None, backend_label)."""
        if not docs or not self.base_url:
            return None, "none"
        try:
            data = await self._post("score", self.settings.sie_rerank_model, {
                "query": {"text": query[:2000]},
                "items": [{"id": i, "text": t[:2000]} for i, t in docs],
            })
            entries = data.get("scores") or []
            raw = {e.get("item_id"): float(e.get("score", 0.0)) for e in entries if e.get("item_id") is not None}
            if not raw:
                raise RuntimeError("SIE score returned no scores")
            lo, hi = min(raw.values()), max(raw.values())
            span = (hi - lo) or 1.0
            self.state.ok()
            self.log.usage("superlinked_sie", "score", len(docs), "items", None,
                           {"model": self.settings.sie_rerank_model})
            return {k: (v - lo) / span if hi != lo else 1.0 for k, v in raw.items()}, f"sie:{self.settings.sie_rerank_model}"
        except Exception as e:  # noqa: BLE001
            self.state.fail(f"{type(e).__name__}: {e}")
            self.log.log("ranking", f"Superlinked SIE score failed; skipping rerank. {self.state.last_error}", "warn", run_id)
            return None, "none"

    async def health(self) -> dict[str, Any]:
        """Bounded connectivity check: GET /v1/models."""
        if not self.base_url:
            return self.state.to_json()
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=10.0) as client:
                r = await client.get(self.base_url + "/v1/models", headers=self._headers())
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            models = [m.get("name") for m in (r.json().get("models") or [])]
            self.state.ok()
            self.state.extra["modelsListed"] = len(models)
        except Exception as e:  # noqa: BLE001
            self.state.fail(f"{type(e).__name__}: {e}")
        return self.state.to_json()
