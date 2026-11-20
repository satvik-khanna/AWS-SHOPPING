"""Optional Senso adapter: read the same rules document (fixtures/rules.json shape) from Senso.

Only runs when SHOPPER_LIVE=1 and SENSO_API_KEY is set. Any error -> None, and the engine falls
back to the local fixtures/rules.json. No sign-up, no paid call happens in tests or offline runs.

Env:
  SENSO_API_KEY          key (required)
  SENSO_BASE_URL         default https://sdk.senso.ai/api/v1  (API shape not verified by us)
  SENSO_RULES_CONTENT_ID id of the content item that holds the rules JSON as its text
"""
from __future__ import annotations

import json
import os
import urllib.request

REQUIRED = ("fx_jpy_per_usd", "buyee_service_fee_jpy", "intl_shipping_us", "us_duty_mfn_by_category", "prohibited")


def _extract(payload) -> dict | None:
    """Accept the rules dict directly, or a content object with the JSON in text/content/body."""
    if isinstance(payload, dict) and all(k in payload for k in REQUIRED):
        return payload
    if isinstance(payload, dict):
        for k in ("text", "content", "body", "raw_text", "data"):
            v = payload.get(k)
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    continue
            got = _extract(v) if v is not None else None
            if got:
                return got
    return None


def fetch_rules(timeout: float = 5.0) -> dict | None:
    key = os.environ.get("SENSO_API_KEY")
    cid = os.environ.get("SENSO_RULES_CONTENT_ID")
    if os.environ.get("SHOPPER_LIVE") != "1" or not key or not cid:
        return None
    base = os.environ.get("SENSO_BASE_URL", "https://sdk.senso.ai/api/v1").rstrip("/")
    req = urllib.request.Request(f"{base}/content/{cid}", headers={"X-API-Key": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _extract(json.loads(r.read().decode("utf-8")))
    except Exception:  # network, auth, shape: fall back offline
        return None
