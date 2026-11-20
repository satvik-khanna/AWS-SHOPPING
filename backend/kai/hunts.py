"""Persistent shopping briefs and changes observed during explicit rechecks.

No background polling or notification delivery. Missing search results are
never interpreted as a price change or a product selling out.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import AgentRun, RankedCandidate, TimelineEvent, new_id, now_iso
from .orchestrator import ShoppingAgent
from .store import Store


def product_key(rc: RankedCandidate) -> str:
    """Keep product/variant query parameters; discard only tracking parameters."""
    c = rc.candidate
    url = c.purchase_url or c.announcement_url
    if not url:
        return f"{'sample' if c.is_fixture else 'real'}:{c.source}:{c.id}"
    p = urlsplit(url)
    query = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"})
    url = urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(query), ""))
    return f"{'sample' if c.is_fixture else 'real'}:{url}"


def snapshots(run: AgentRun) -> dict[str, dict[str, Any]]:
    _, avoided = run.request.preference_signals(run.memory)
    result: dict[str, dict[str, Any]] = {}
    for rc in run.ranked:
        c, cost = rc.candidate, rc.cost
        key = product_key(rc)
        if key in result:  # the highest ranked view of a product is the baseline
            continue
        eligible = bool(rc.purchase_path == "PROXY_REQUEST_DRAFT" and cost and cost.total_usd is not None
                        and cost.within_budget != "no" and not set(c.attributes).intersection(avoided)
                        and not c.injection_flags)
        result[key] = {"candidateId": c.id, "title": c.title, "url": c.purchase_url or c.announcement_url,
                       "priceJpy": c.price_jpy, "availability": c.availability,
                       "totalUsd": cost.total_usd if cost else None, "matchesBrief": eligible,
                       "isFixture": c.is_fixture}
    return result


def changes_since(previous: dict[str, dict[str, Any]], current: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    changes = []
    for key, item in current.items():
        old = previous.get(key)
        kinds = []
        if old is None:
            if item["matchesBrief"]:
                kinds.append("NEW_MATCH")
        else:
            if old["availability"] != item["availability"]:
                kinds.append("RESTOCK" if old["availability"] == "SOLD_OUT" and item["availability"] == "IN_STOCK"
                             else "AVAILABILITY_CHANGED")
            if old["priceJpy"] is not None and item["priceJpy"] is not None and old["priceJpy"] != item["priceJpy"]:
                kinds.append("PRICE_DROP" if item["priceJpy"] < old["priceJpy"] else "PRICE_INCREASE")
            # Suppress tiny currency fluctuations; item-price changes are always reported above.
            if old["totalUsd"] is not None and item["totalUsd"] is not None \
                    and abs(item["totalUsd"] - old["totalUsd"]) >= max(1.0, old["totalUsd"] * 0.01):
                kinds.append("DELIVERED_COST_CHANGED")
            if not old["matchesBrief"] and item["matchesBrief"]:
                kinds.append("NOW_MATCHES_BRIEF")
        if kinds:
            changes.append({"kinds": kinds, "before": old, "after": item})
    return changes


class HuntService:
    def __init__(self, store: Store, agent: ShoppingAgent):
        self.store = store
        self.agent = agent
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def public(doc: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in doc.items() if k != "snapshots"}

    def list(self) -> list[dict[str, Any]]:
        return [self.public(d) for d in self.store.list("hunt")]

    def create(self, run_id: str, title: str | None = None) -> dict[str, Any]:
        data = self.store.get("run", run_id)
        if not data:
            raise KeyError("Run not found")
        run = AgentRun.model_validate(data)
        existing = next((h for h in self.store.list("hunt") if h["message"] == run.request.raw), None)
        if existing:
            return self.public(existing)
        doc = {"id": new_id("hunt"), "title": (title or "").strip() or run.request.category_label,
               "message": run.request.raw, "createdAt": now_iso(), "lastCheckedAt": run.created_at,
               "lastRunId": run.id, "snapshots": snapshots(run), "checks": [], "lastCheck": None}
        self.store.put("hunt", doc["id"], doc)
        self.agent.log.log("hunt", f"Saved hunt: {doc['title']}. Rechecks run only when requested.", "ok", run.id)
        return self.public(doc)

    def delete(self, hunt_id: str) -> bool:
        lock = self._locks.get(hunt_id)
        if lock and lock.locked():
            raise ValueError("This hunt is being checked. Wait for the check to finish.")
        return self.store.delete("hunt", hunt_id)

    async def check(self, hunt_id: str) -> dict[str, Any]:
        lock = self._locks.setdefault(hunt_id, asyncio.Lock())
        if lock.locked():
            raise ValueError("This hunt is already being checked.")
        async with lock:
            doc = self.store.get("hunt", hunt_id)
            if not doc:
                raise KeyError("Hunt not found")
            run = await self.agent.run(doc["message"], prepare_draft=False, refresh_imports=True,
                                       learn_preferences=False)
            if not isinstance(run, AgentRun):
                raise ValueError(run.get("message", "Could not understand the saved brief"))
            current = snapshots(run)
            changes = changes_since(doc["snapshots"], current)
            d = run.discovery
            check = {"at": now_iso(), "runId": run.id, "changes": changes,
                     "candidateCount": len(current), "matchCount": sum(i["matchesBrief"] for i in current.values()),
                     "sampleCount": sum(i["isFixture"] for i in current.values()),
                     "freshXDiscovery": bool(d and d.fresh_discovery),
                     "cachedX": bool(d and any(s.from_cache for s in d.provider_statuses)),
                     "note": "Changes compare observed results with this hunt's previous observations. "
                             "Missing results do not prove an item sold out. Posts and pages may be cached for "
                             "15 minutes; listing timestamps appear in the comparison. Checks run on demand."}
            doc["snapshots"].update(current)  # preserve unseen products across partial/failed searches
            doc.update(lastCheck=check, lastCheckedAt=check["at"], lastRunId=run.id)
            doc["checks"] = ([check] + doc["checks"])[:20]
            self.store.put("hunt", hunt_id, doc)
            self.agent.log.log("hunt", f"Checked {doc['title']}: {len(changes)} observed change(s), "
                               f"{check['matchCount']} match(es). No draft created.", "ok", run.id,
                               {"huntId": hunt_id, "changes": changes})
            run.timeline = [TimelineEvent.model_validate(e) for e in reversed(self.store.events(run.id))]
            self.store.put("run", run.id, run.to_json())
            return {"hunt": self.public(doc), "check": check, "run": run.to_json()}
