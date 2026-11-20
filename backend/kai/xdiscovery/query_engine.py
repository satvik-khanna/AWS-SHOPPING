"""Japanese announcement query engine.

Builds 3-6 targeted queries per discovery pass, one per (term, intent) so
results for different intents are not mixed, and validates each against the
X API v2 recent-search query rules before anything is sent."""

from __future__ import annotations

import re
from typing import Any

from ..models import XQuery
from ..vocab import ATTRIBUTES, CATEGORIES, INTENTS

X_QUERY_MAX_LEN = 512  # recent search, self-serve / pay-per-use access
MIN_QUERIES, MAX_QUERIES = 3, 6

# Operators that cannot stand alone; they need at least one keyword / from: term.
_CONJUNCTION_REQUIRED = re.compile(r"^-?(lang|has|is):[a-z_]+$")
_KNOWN_OPERATOR = re.compile(r"^-?(lang|has|is|from|to|url|conversation_id|context|entity|place|place_country):")
_ALLOWED_IS = {"retweet", "reply", "quote", "verified", "nullcast"}
_ALLOWED_HAS = {"links", "media", "images", "videos", "hashtags", "cashtags", "mentions", "geo", "video_link"}
_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

# Japanese term used in queries for a preferred attribute.
_ATTR_QUERY_TERM = {"handmade": "ハンドメイド", "matte": "マット", "resin": "レジン", "ceramic": "陶器",
                    "wood": "木製", "metal": "真鍮", "limited": "数量限定", "translucent": "クリア"}


def _q(term: str) -> str:
    return '"' + term.replace('"', "") + '"'


def _group(terms: list[str]) -> str:
    return "(" + " OR ".join(terms) + ")"


def validate_x_query(text: str, max_len: int = X_QUERY_MAX_LEN) -> list[str]:
    """Return a list of problems; empty means the query is acceptable for
    GET /2/tweets/search/recent."""
    errors: list[str] = []
    q = text.strip()
    if not q:
        return ["query is empty"]
    if len(q) > max_len:
        errors.append(f"query is {len(q)} characters; the limit is {max_len}")
    if q.count('"') % 2:
        errors.append("unbalanced double quotes")
    depth = 0
    for ch in q:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                break
    if depth != 0:
        errors.append("unbalanced parentheses")
    if re.search(r"\(\s*\)", q):
        errors.append("empty group ()")
    if re.search(r"(^|\(|\bOR)\s*OR\b", q) or re.search(r"\bOR\s*($|\))", q):
        errors.append("OR is missing an operand")
    if re.search(r"\bAND\b", q):
        errors.append("AND is not an operator; use a space")

    # Tokenise outside quotes to inspect operators.
    unquoted = re.sub(r'"[^"]*"', ' "x" ', q)
    tokens = [t for t in re.split(r"[\s()]+", unquoted) if t and t != "OR"]
    standalone = [t for t in tokens if not _CONJUNCTION_REQUIRED.match(t) and not t.startswith("-")]
    if not standalone:
        errors.append("needs at least one keyword or from: term; lang:/has:/is: cannot stand alone")
    for t in tokens:
        m = re.match(r"^-?(is|has):([a-z_]+)$", t)
        if m:
            allowed = _ALLOWED_IS if m.group(1) == "is" else _ALLOWED_HAS
            if m.group(2) not in allowed:
                errors.append(f"unsupported operator value: {t}")
        elif ":" in t and not t.startswith(("http", '"')) and re.match(r"^-?[a-z_]+:", t) and not _KNOWN_OPERATOR.match(t):
            errors.append(f"unknown operator: {t.split(':')[0]}:")
        m = re.match(r"^-?(from|to):(.+)$", t)
        if m and not _HANDLE.match(m.group(2)):
            errors.append(f"invalid handle in {t}")
    return errors


def to_indexed_query(x_query: str) -> str:
    """Translate an X API query into a plain web-search query restricted to
    publicly indexed x.com posts (operators are X-specific and are dropped)."""
    q = re.sub(r"-?\b(lang|has|is):[a-z_]+", " ", x_query)
    q = re.sub(r"\bfrom:([A-Za-z0-9_]{1,15})", r"\1", q)
    q = re.sub(r"\s+", " ", q).strip()
    return f"site:x.com {q}"


def build_queries(category: str, preferred_attributes: list[str] | None = None,
                  history: list[dict[str, Any]] | None = None, watched: list[dict[str, Any]] | None = None,
                  extra_terms: list[str] | None = None, max_queries: int = MAX_QUERIES,
                  ja_terms: list[str] | None = None) -> list[XQuery]:
    """`ja_terms` overrides the category's built-in Japanese terms (used for
    categories KAI has no vocabulary for, where the user or an LLM supplied the terms)."""
    spec = CATEGORIES.get(category)
    terms: list[str] = [t for t in (ja_terms or (spec["ja"] if spec else [])) if t.strip()]
    if not terms:
        return []
    label = spec["label"] if spec else terms[0]
    primary = terms[0]
    out: list[XQuery] = []
    seen: set[str] = set()

    def add(text: str, intent: str, source: str, rationale: str) -> None:
        if text in seen or len(out) >= max_queries:
            return
        seen.add(text)
        errors = validate_x_query(text)
        out.append(XQuery(text=text, category=category, intent=intent, source=source, rationale=rationale,
                          valid=not errors, errors=errors))

    # 1-3: core category intents, each a separate search.
    for intent in ("release", "on_sale", "preorder_lottery"):
        i = INTENTS[intent]
        add(f"{_q(primary)} {_group(i['terms'])} {i['suffix']}", intent, "category",
            f"{label}: {i['label']}")

    # Stored preferences sharpen one query (e.g. handmade + matte).
    pref_terms = [_ATTR_QUERY_TERM[a] for a in (preferred_attributes or []) if a in _ATTR_QUERY_TERM][:2]
    if pref_terms:
        labels = ", ".join([ATTRIBUTES[a]["label"] for a in (preferred_attributes or []) if a in _ATTR_QUERY_TERM][:2])
        add(f"{_q(primary)} {' '.join(_q(t) for t in pref_terms)} {_group(['新作', '販売開始', '再販', '受注生産'])} lang:ja -is:retweet",
            "release", "preference", f"Narrowed by stored preference: {labels}")

    # Watched creators and saved search terms for this category.
    for w in watched or []:
        cats = w.get("categories") or []
        if cats and category not in cats:
            continue
        handle = (w.get("handle") or "").lstrip("@")
        if handle and _HANDLE.match(handle):
            add(f"from:{handle} {_group(['新作', '販売開始', '予約開始', '抽選販売', '再販', '入荷'])} -is:retweet",
                "watched_creator", "watched_creator", f"Watched creator @{handle}")
        for term in (w.get("searchTerms") or [])[:2]:
            term = term.strip()
            if term:
                add(f"{_q(term)} {_group(INTENTS['release']['terms'] + ['販売開始'])} lang:ja -is:retweet",
                    "release", "watched_term", f"Saved search term: {term}")

    # A secondary category term the user has searched this category before with, or the next synonym.
    past_same = [h for h in (history or []) if h.get("category") == category]
    if len(terms) > 1:
        why = (f"Category searched {len(past_same)} time(s) before; widening to a related term"
               if past_same else "Related term for wider coverage")
        add(f"{_q(terms[1])} {_group(INTENTS['release']['terms'] + ['販売開始'])} lang:ja -is:retweet",
            "release", "history" if past_same else "category", why)

    for term in extra_terms or []:
        add(f"{_q(term)} {_group(INTENTS['on_sale']['terms'])} lang:ja has:links -is:retweet", "on_sale", "category",
            f"Request-specific term: {term}")

    add(f"{_q(primary)} {_group(INTENTS['limited_firstcome']['terms'])} {INTENTS['limited_firstcome']['suffix']}",
        "limited_firstcome", "category", f"{label}: {INTENTS['limited_firstcome']['label']}")

    return out[:max_queries]
