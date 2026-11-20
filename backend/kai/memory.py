"""User memory: preferences and search history.

Structured preference facts always live in the local store (ranking needs
structured signals). When Cognee is live, each fact is also written to
Cognee's graph memory and recall results are merged in."""

from __future__ import annotations

import re
from typing import Any

from .integrations.cognee_memory import CogneeAdapter
from .models import MemoryRecall, new_id, now_iso
from .observability import ActivityLog
from .store import Store
from .textutil import nfkc
from .vocab import ATTRIBUTES, detect_attributes

_NEG = re.compile(r"\b(don'?t (like|want)|do not (like|want)|dislike|avoid|hate|no |not |never|without)\b|嫌い|苦手|不要|なし|避け", re.I)
_PREF = re.compile(r"\b(prefer|like|love|want|into|fan of|favou?rite|looking for|only|always)\b|好き|好み|欲しい|がいい", re.I)


class MemoryService:
    def __init__(self, store: Store, log: ActivityLog, cognee: CogneeAdapter):
        self.store = store
        self.log = log
        self.cognee = cognee

    # ---- preferences -----------------------------------------------------
    def list_preferences(self, user_id: str = "default") -> list[dict[str, Any]]:
        return [p for p in self.store.list("preference") if p.get("userId") == user_id]

    async def add_preference(self, text: str, user_id: str = "default", source: str = "user_stated",
                             run_id: str | None = None) -> list[dict[str, Any]]:
        """Store a preference statement. Returns the structured facts derived from it."""
        text = text.strip()
        if not text:
            return []
        facts = self._facts_from_text(text)
        saved = []
        existing = {(p["attribute"], p["polarity"]) for p in self.list_preferences(user_id)}
        for attr, polarity in facts:
            if (attr, polarity) in existing:
                continue
            # a new statement about an attribute replaces the opposite one
            for p in self.list_preferences(user_id):
                if p["attribute"] == attr and p["polarity"] != polarity:
                    self.store.delete("preference", p["id"])
            doc = {"id": new_id("pref"), "userId": user_id, "attribute": attr, "polarity": polarity,
                   "label": ATTRIBUTES[attr]["label"], "text": text, "source": source, "createdAt": now_iso()}
            self.store.put("preference", doc["id"], doc)
            saved.append(doc)
        if not facts:
            doc = {"id": new_id("pref"), "userId": user_id, "attribute": None, "polarity": "note",
                   "label": None, "text": text, "source": source, "createdAt": now_iso()}
            self.store.put("preference", doc["id"], doc)
            saved.append(doc)
        if saved:
            in_cognee = await self.cognee.remember(f"User shopping preference: {text}", user_id, run_id)
            for doc in saved:
                doc["inCognee"] = in_cognee
                self.store.put("preference", doc["id"], doc)
            self.log.log("memory", f"Stored {len(saved)} preference fact(s) "
                                   f"({'Cognee + local' if in_cognee else 'local store only'}): {text[:80]}", "ok", run_id,
                         {"facts": [{"attribute": d["attribute"], "polarity": d["polarity"]} for d in saved]})
        return saved

    def delete_preference(self, pref_id: str) -> bool:
        return self.store.delete("preference", pref_id)

    @staticmethod
    def _facts_from_text(text: str) -> list[tuple[str, str]]:
        """Split on clause boundaries so 'handmade matte, not glossy' yields
        like:handmade, like:matte, avoid:glossy."""
        facts: list[tuple[str, str]] = []
        t = nfkc(text)
        # Keep the negation in its clause: consuming "and not" used to turn
        # "matte and not glossy" into two positive facts.
        clauses = re.split(r"[,;.。、]|\bbut\b|\band (?=not\b)|\b(?=not (?!only\b))|けど|が、", t,
                           flags=re.I)
        for clause in clauses:
            attrs = detect_attributes(clause)
            if not attrs:
                continue
            # Scope local adjective negation to its attribute. In "not glossy
            # artisan keycaps", "not" describes glossy, not artisan. A taste
            # verb ("I avoid glossy handmade pieces") covers the whole clause.
            lowered = clause.lower()
            positions = sorted((min((lowered.find(term.lower()), len(term))
                                    for term in ATTRIBUTES[a]["terms"] if term.lower() in lowered), a)
                               for a in attrs)
            neg = bool(re.search(r"嫌い|苦手|不要|なし|避け", clause))
            local_negation = False
            last_end = 0
            for ((start, length), a) in positions:
                between = clause[last_end:start]
                negatives = list(_NEG.finditer(between + "x"))  # preserve the boundary before the attribute
                if negatives and not re.search(r"\bnot only\b", between, re.I):
                    neg = True
                    local_negation = bool(re.fullmatch(r"not\s*|no\s*|without", negatives[-1].group(), re.I))
                elif _PREF.search(between):
                    neg, local_negation = False, False
                elif last_end and local_negation and not re.search(r"\b(and|or)\b|と|や", between, re.I):
                    neg, local_negation = False, False
                fact = (a, "avoid" if neg else "like")
                if fact not in facts:
                    facts.append(fact)
                last_end = start + length
        return list(dict(facts).items())  # the final statement about an attribute wins

    @staticmethod
    def looks_like_preference(text: str) -> bool:
        return bool(_PREF.search(text) or _NEG.search(text))

    # ---- search history --------------------------------------------------
    def record_search(self, raw: str, category: str, user_id: str = "default") -> None:
        doc = {"id": new_id("srch"), "userId": user_id, "raw": raw, "category": category, "ts": now_iso()}
        self.store.put("search_history", doc["id"], doc)

    def past_searches(self, user_id: str = "default", limit: int = 10) -> list[dict[str, Any]]:
        return [s for s in self.store.list("search_history", limit=50) if s.get("userId") == user_id][:limit]

    # ---- recall ----------------------------------------------------------
    async def recall(self, query: str, user_id: str = "default", run_id: str | None = None) -> MemoryRecall:
        prefs = self.list_preferences(user_id)
        preferred = [p["attribute"] for p in prefs if p["polarity"] == "like" and p["attribute"]]
        avoided = [p["attribute"] for p in prefs if p["polarity"] == "avoid" and p["attribute"]]
        facts = sorted({p["text"] for p in prefs})
        cognee_results = await self.cognee.recall(f"What are this user's shopping preferences relevant to: {query}",
                                                  user_id, run_id=run_id)
        backend = "fallback"
        reason = self.cognee.state.to_json()["detail"]
        if cognee_results is not None:
            backend = "cognee"
            reason = "Recalled from Cognee graph memory and merged with the local structured preference store."
            # attributes mentioned in Cognee's recall that the local store does not have yet
            for text in cognee_results:
                for attr, polarity in self._facts_from_text(text):
                    target = preferred if polarity == "like" else avoided
                    other = avoided if polarity == "like" else preferred
                    if attr not in target and attr not in other:
                        target.append(attr)
        return MemoryRecall(
            backend=backend, backend_reason=reason, preferred=list(dict.fromkeys(preferred)),
            avoided=list(dict.fromkeys(avoided)), facts=facts, cognee_results=cognee_results or [],
            past_searches=[s["raw"] for s in self.past_searches(user_id, 5)],
        )
