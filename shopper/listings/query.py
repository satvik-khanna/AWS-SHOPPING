"""English (or Japanese) query -> concepts with Japanese keyword expansion, and relevance matching.

    plan = parse_query("used Fujifilm X100V under $900")
    plan.ja_keywords      -> ["X100V", "富士フイルム X100V"]
    relevance(plan, listing).score  -> 0..1
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..models import Listing
from .lexicon import ENTRIES, STOPWORDS

WEIGHTS = {"model": 3.0, "name": 2.0, "brand": 1.5, "term": 1.5, "type": 1.0, "color": 0.5}
MIN_RELEVANCE = 0.5
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def norm(s: str) -> str:
    """NFKC (full-width -> half-width, half-width kana -> full) + lowercase."""
    return unicodedata.normalize("NFKC", s or "").lower()


def has_japanese(s: str) -> bool:
    return bool(_CJK.search(s or ""))


def _pattern(form: str, loose_model: bool = False) -> re.Pattern:
    f = norm(form)
    if has_japanese(f):
        return re.compile(r"\s*".join(re.escape(p) for p in f.split()))
    if loose_model:      # x100v also matches "x100 v" / "x-100v", but never "x100vi"
        parts = re.findall(r"[a-z]+|\d+|[^a-z\d]+", f)
        body = r"[\s\-]?".join(re.escape(p) for p in parts if p.strip())
    else:
        body = r"[\s\-_.]*".join(re.escape(p) for p in re.split(r"[\s\-_.]+", f) if p)
    return re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])")


@dataclass
class Concept:
    key: str                    # display name, e.g. "Fujifilm" or "x100v"
    kind: str                   # model | name | brand | term | type | color
    forms: list[str]            # every surface form we match (English + Japanese)
    ja: str | None = None       # preferred Japanese keyword, if any
    patterns: list[re.Pattern] = field(default_factory=list, repr=False)

    @property
    def weight(self) -> float:
        return WEIGHTS.get(self.kind, 1.0)

    def found_in(self, text: str) -> bool:
        return any(p.search(text) for p in self.patterns)


@dataclass
class QueryPlan:
    raw: str
    concepts: list[Concept]
    size_cm: float | None = None        # shoe size asked for in the query
    wants_junk: bool = False            # "junk" / "for parts" asked for explicitly

    @property
    def required(self) -> list[Concept]:
        return [c for c in self.concepts if c.kind == "model"]

    @property
    def ja_keywords(self) -> list[str]:
        """Search strings for a Japanese marketplace, most specific first."""
        models = [c.ja or c.key.upper() for c in self.concepts if c.kind == "model"]
        rest = [c.ja or c.key for c in self.concepts if c.kind in ("brand", "name", "term", "type")]
        out = []
        if models:
            out.append(" ".join(models))
        full = " ".join(rest + models)
        if full and full not in out:
            out.append(full)
        return out

    @property
    def expansions(self) -> dict[str, list[str]]:
        return {c.key: c.forms for c in self.concepts}


def _lexicon() -> list[tuple[str, re.Pattern, tuple]]:
    rows = []
    for entry in ENTRIES:
        display, _kind, ja_forms, en_forms = entry
        for f in [display] + en_forms + ja_forms:
            rows.append((norm(f), _pattern(f), entry))
    rows.sort(key=lambda r: len(r[0]), reverse=True)        # longest form first
    return rows


_LEX = _lexicon()
_BUDGET = re.compile(r"(?:under|below|less than|max|up to|<)\s*(?:us)?\$?\s*\d[\d,.]*\s*(?:usd|dollars?|bucks)?|(?:us)?\$\s*\d[\d,.]*")
_SHIP = re.compile(r"(?:shipp?(?:ed|ing)?|deliver(?:ed|y)?)\s+to\s+(?:the\s+)?[\w\-]+")
_SIZE_CM = re.compile(r"(?:size\s*)?\b(2[0-9]|3[0-2])(\.\d)?\s*cm\b")
_SIZE_US = re.compile(r"\bus\s*(?:men'?s\s*)?(\d{1,2}(?:\.5)?)\b")


def parse_query(query: str) -> QueryPlan:
    q = norm(query)
    wants_junk = bool(re.search(r"junk|for parts|ジャンク|部品取り", q))
    q = _BUDGET.sub(" ", q)
    q = _SHIP.sub(" ", q)
    size = None
    m = _SIZE_CM.search(q)
    if m:
        size = float(m.group(1) + (m.group(2) or ""))
        q = q[:m.start()] + " " + q[m.end():]
    else:
        m = _SIZE_US.search(q)
        if m:                                   # Nike men's chart: JP cm = US + 18
            size = float(m.group(1)) + 18.0
            q = q[:m.start()] + " " + q[m.end():]

    found: list[tuple[int, Concept]] = []
    seen = set()
    for _form, pat, (display, kind, ja_forms, en_forms) in _LEX:
        if display in seen:
            continue
        m = pat.search(q)
        if not m:
            continue
        seen.add(display)
        forms = [display] + en_forms + ja_forms
        found.append((m.start(), Concept(display, kind, forms, ja_forms[0] if ja_forms else None,
                                         [_pattern(f) for f in forms])))
        q = q[:m.start()] + " " * (m.end() - m.start()) + q[m.end():]     # consume the span

    for m in re.finditer(r"[a-z0-9][a-z0-9.\-]*[a-z0-9]|[a-z0-9]|[\u3040-\u30ff\u3400-\u9fff]+", q):
        tok = m.group(0)
        if tok in STOPWORDS or len(tok) < 2 or tok in seen:
            continue
        seen.add(tok)
        is_model = bool(re.search(r"\d", tok) and re.search(r"[a-z]", tok)) or (tok.isdigit() and len(tok) >= 3)
        found.append((m.start(), Concept(tok, "model" if is_model else "term", [tok],
                                         tok if has_japanese(tok) else None,
                                         [_pattern(tok, loose_model=is_model)])))
    found.sort(key=lambda t: t[0])
    return QueryPlan(raw=query, concepts=[c for _, c in found], size_cm=size, wants_junk=wants_junk)


def expand_query(query: str) -> list[str]:
    """English query -> Japanese marketplace keywords, e.g. 'Nike Dunk Low' -> ['ダンク ロー', 'ナイキ ダンク ロー']."""
    return parse_query(query).ja_keywords


def listing_text(l: Listing) -> str:
    return norm(" ".join([l.title_ja, l.title_en, l.category or "", " ".join(str(v) for v in l.fields.values())]))


@dataclass
class Relevance:
    score: float                # 0..1 weighted share of query concepts found in the listing
    matched: list[str]
    missing: list[str]
    required_ok: bool           # every model token in the query is present

    @property
    def ok(self) -> bool:
        return self.required_ok and self.score >= MIN_RELEVANCE


def relevance(plan: QueryPlan, listing: Listing) -> Relevance:
    if not plan.concepts:
        return Relevance(1.0, [], [], True)
    text = listing_text(listing)
    matched, missing, got, total, required_ok = [], [], 0.0, 0.0, True
    for c in plan.concepts:
        total += c.weight
        if c.found_in(text):
            got += c.weight
            matched.append(c.key)
        else:
            missing.append(c.key)
            if c.kind == "model":
                required_ok = False
    return Relevance(round(got / total, 4), matched, missing, required_ok)
