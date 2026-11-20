"""Optional LLM client (OpenAI-compatible or Anthropic) for translation and
structured extraction. When no key is configured, `available` is False and
callers use labeled heuristics instead. Token usage is always logged; dollar
cost is logged only when per-token prices are configured."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import Settings
from .models import now_iso
from .observability import ActivityLog


class LLMClient:
    def __init__(self, settings: Settings, log: ActivityLog, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.log = log
        self._transport = transport
        self.provider: str | None = None
        self.model: str | None = None
        if settings.openai_api_key:
            self.provider = "openai_compatible"
            self.model = settings.llm_model or "gpt-4o-mini"
        elif settings.anthropic_api_key:
            self.provider = "anthropic"
            self.model = settings.llm_model or "claude-haiku-4-5"
        self.calls = 0
        self.failures = 0
        self.last_error: str | None = None
        self.last_success_at: str | None = None

    @property
    def available(self) -> bool:
        return self.provider is not None

    def status(self) -> dict[str, Any]:
        if not self.available:
            mode, detail = "fallback", "No LLM key configured; using heuristic extraction and glossary glosses (no real translation)."
        elif self.last_success_at:
            mode, detail = "live", f"{self.provider} / {self.model}"
        elif self.last_error:
            mode, detail = "error", f"{self.provider} / {self.model}: {self.last_error}"
        else:
            mode, detail = "configured_untested", f"{self.provider} / {self.model} configured; no call made yet."
        return {"name": "llm", "label": "LLM (translation + extraction)", "mode": mode, "configured": self.available,
                "detail": detail, "calls": self.calls, "failures": self.failures,
                "lastSuccessAt": self.last_success_at, "lastError": self.last_error}

    async def complete_json(self, system: str, user: str, max_tokens: int = 900,
                            purpose: str = "extract", run_id: str | None = None) -> dict[str, Any] | None:
        """Return a parsed JSON object, or None on any failure (never raises)."""
        if not self.available:
            return None
        self.calls += 1
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=30.0) as client:
                if self.provider == "openai_compatible":
                    text, usage = await self._openai(client, system, user, max_tokens)
                else:
                    text, usage = await self._anthropic(client, system, user, max_tokens)
        except Exception as e:  # noqa: BLE001 - reported, never raised
            self.failures += 1
            self.last_error = f"{type(e).__name__}: {e}"[:300]
            self.log.log("llm", f"LLM call failed ({purpose}): {self.last_error}", "error", run_id)
            return None

        tin, tout = usage
        cost = None
        s = self.settings
        if s.llm_input_usd_per_mtok is not None and s.llm_output_usd_per_mtok is not None:
            cost = (tin * s.llm_input_usd_per_mtok + tout * s.llm_output_usd_per_mtok) / 1_000_000
        self.log.usage("llm", purpose, tin + tout, "tokens", cost,
                       {"provider": self.provider, "model": self.model, "inputTokens": tin, "outputTokens": tout})
        parsed = _parse_json(text)
        if parsed is None:
            self.failures += 1
            self.last_error = "Model did not return valid JSON"
            self.log.log("llm", f"LLM returned non-JSON output ({purpose})", "warn", run_id)
            return None
        self.last_success_at = now_iso()
        self.last_error = None
        return parsed

    async def _openai(self, client: httpx.AsyncClient, system: str, user: str, max_tokens: int) -> tuple[str, tuple[int, int]]:
        url = f"{self.settings.openai_base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}", "Content-Type": "application/json"}
        body: dict[str, Any] = {
            "model": self.model, "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        r = await client.post(url, headers=headers, json=body)
        if r.status_code == 400:
            # Some OpenAI-compatible gateways reject response_format; retry once without it.
            body.pop("response_format", None)
            r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        usage = data.get("usage") or {}
        return data["choices"][0]["message"]["content"] or "", (
            int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))

    async def _anthropic(self, client: httpx.AsyncClient, system: str, user: str, max_tokens: int) -> tuple[str, tuple[int, int]]:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.settings.anthropic_api_key or "", "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": self.model, "max_tokens": max_tokens, "system": system,
                  "messages": [{"role": "user", "content": user}]},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage") or {}
        return text, (int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))


def _parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start >= 0 and end > start:
        try:
            v = json.loads(t[start : end + 1])
            return v if isinstance(v, dict) else None
        except json.JSONDecodeError:
            return None
    return None
