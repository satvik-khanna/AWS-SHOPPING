"""Offline ranker: relevance + landed cost + condition + user preferences, with a reason per pick.

score = 0.40 * relevance            share of query concepts found in the listing (shopper.listings)
      + 0.30 * value                0.5 * budget headroom + 0.5 * cheapness vs comparable picks
      + 0.20 * condition            junk 0 .. new 1
      + preference / evidence adjustments (size, notes, past purchases, flaws, tested, box, ...)
      - penalties                   accessory-only listing, junk not asked for, wrong size

Candidates below the user's condition floor are dropped, as are rejected sellers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..listings.query import parse_query, relevance
from ..models import LandedCost, Listing, Preferences, RankedPick, ShoppingRequest

CONDITION_ORDER = ["junk", "fair", "good", "like_new", "new"]
CONDITION_WORDS = {"new": "new/unused", "like_new": "like new", "good": "good (no notable marks)",
                   "fair": "fair (some wear)", "junk": "junk / for parts"}
W_REL, W_VALUE, W_COND = 0.40, 0.30, 0.20
_ACCESSORY_ASK = re.compile(r"case|strap|hood|filter|adapter|charger|battery|grip|cap|lens cap|"
                            r"box|parts|ケース|ストラップ|フード|フィルター|バッテリー|箱")

# preference note keyword -> (field, wanted value, bonus, reason text)
NOTE_RULES = [
    (r"\bbox|箱", "box", "yes", 0.05, "box included"),
    (r"tested|working|動作", "tested", "working", 0.05, "seller says tested working"),
    (r"original press|original|初版|オリジナル", "edition", "original", 0.06, "original pressing"),
    (r"\bobi\b|帯", "obi", "yes", 0.05, "obi strip included"),
    (r"sealed|未開封", "sealed", "yes", 0.05, "still sealed"),
]


def _cond_idx(c: str) -> int:
    return CONDITION_ORDER.index(c) if c in CONDITION_ORDER else 0


def _wanted_size(plan_size: float | None, prefs: Preferences, category: str | None) -> float | None:
    if plan_size:
        return plan_size
    if category != "sneakers":
        return None
    for k in ("shoe_cm", "shoe", "sneakers", "shoes"):
        v = prefs.sizes.get(k)
        if not v:
            continue
        m = re.search(r"(\d{2}(?:\.\d)?)", str(v))
        if m:
            n = float(m.group(1))
            if "us" in str(v).lower() or n < 18:     # US men's -> JP cm (Nike chart: +18)
                n += 18.0
            return n
    return None


@dataclass
class Parts:
    """Score breakdown for one candidate (exposed for tests and the demo trail)."""
    relevance: float
    value: float
    condition: float
    adjust: float = 0.0
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    drop: str | None = None

    @property
    def total(self) -> float:
        return round(W_REL * self.relevance + W_VALUE * self.value + W_COND * self.condition
                     + self.adjust, 4)


def score_parts(req: ShoppingRequest, prefs: Preferences,
                candidates: list[tuple[Listing, LandedCost]]) -> list[tuple[Listing, LandedCost, Parts]]:
    plan = parse_query(req.query)
    floor = req.condition_min or prefs.condition_min
    wants_acc = bool(_ACCESSORY_ASK.search(req.query.lower()))
    budget = max(req.max_total_usd, 1.0)

    def is_main(l: Listing) -> bool:
        return l.fields.get("item_type") != "accessory" and (l.condition != "junk" or plan.wants_junk)

    peers = [c.total_usd for l, c in candidates if is_main(l)] or [c.total_usd for _, c in candidates]
    lo, hi = (min(peers), max(peers)) if peers else (0.0, 0.0)
    notes = " ".join(prefs.notes).lower()
    past = {p.lower() for p in prefs.past_purchases}

    out = []
    for l, c in candidates:
        rel = relevance(plan, l)
        headroom = max(budget - c.total_usd, 0) / budget
        cheap = 1.0 if hi <= lo else max(0.0, min(1.0, (hi - c.total_usd) / (hi - lo)))
        p = Parts(relevance=rel.score, value=round(0.5 * headroom + 0.5 * cheap, 4),
                  condition=_cond_idx(l.condition) / 4)

        if l.seller in prefs.rejected_sellers:
            p.drop = f"seller {l.seller} was rejected before"
        if floor in CONDITION_ORDER and _cond_idx(l.condition) < CONDITION_ORDER.index(floor):
            p.drop = p.drop or f"condition {l.condition} below your minimum {floor}"
        if not rel.required_ok:
            p.adjust -= 0.5
            p.cons.append(f"does not mention {', '.join(m for m in rel.missing)}")
        if l.fields.get("item_type") == "accessory" and not wants_acc:
            p.adjust -= 0.6
            p.cons.append("accessory / box only, not the item itself")
        if l.condition == "junk" and not plan.wants_junk:
            p.adjust -= 0.4
            p.cons.append("sold as junk / for parts")

        want = _wanted_size(plan.size_cm, prefs, l.category)
        have = l.fields.get("size_cm")
        if want and have:
            if abs(float(have) - want) < 0.01:
                p.adjust += 0.15
                p.pros.append(f"your size ({have} cm)")
            else:
                p.adjust -= 0.5
                p.cons.append(f"size {have} cm, you wear {want:g} cm")
        elif want and l.category == "sneakers":
            p.cons.append("size not stated")

        for pat, key, val, bonus, text in NOTE_RULES:
            if notes and re.search(pat, notes) and l.fields.get(key) == val:
                p.adjust += bonus
                p.pros.append(text + " (as you prefer)")
        if l.fields.get("tested") == "working" and not any(t.startswith("seller says tested") for t in p.pros):
            p.adjust += 0.02
            p.pros.append("tested working")
        sc = l.fields.get("shutter_count")
        if sc and sc.isdigit():
            if int(sc) <= 5000:
                p.adjust += 0.03
                p.pros.append(f"low shutter count ({int(sc):,})")
            elif int(sc) >= 30000:
                p.adjust -= 0.03
                p.cons.append(f"high shutter count ({int(sc):,})")
        if l.fields.get("flaws"):
            n = len(l.fields["flaws"].split(","))
            p.adjust -= min(0.05 * n, 0.15)
            p.cons.append(f"flaws noted: {l.fields['flaws']}")
        if l.fields.get("condition_source", "").startswith("not stated"):
            p.cons.append("condition not stated by seller")
        if l.id.lower() in past or l.title_ja.lower() in past or l.title_en.lower() in past:
            p.adjust -= 0.2
            p.cons.append("you already bought this one")
        out.append((l, c, p))
    return out


def _reason(req: ShoppingRequest, l: Listing, c: LandedCost, p: Parts, rank: int,
            next_cost: float | None, matched: list[str]) -> str:
    under = req.max_total_usd - c.total_usd
    bits = []
    if matched:
        bits.append(f"matches {', '.join(matched)}")
    money = f"${c.total_usd:.0f} landed, ${under:.0f} under budget"
    if rank == 0 and next_cost is not None and next_cost > c.total_usd:
        money += f", ${next_cost - c.total_usd:.0f} less than the next pick"
    bits.append(money)
    cond = f"condition {CONDITION_WORDS.get(l.condition, l.condition)}"
    if l.fields.get("condition_ja"):
        cond += f" (seller grade: {l.fields['condition_ja']})"
    bits.append(cond)
    bits += p.pros
    text = "; ".join(bits)
    if p.cons:
        text += ". Watch out: " + "; ".join(p.cons)
    return text


class SmartRanker:
    def rank(self, req: ShoppingRequest, prefs: Preferences,
             candidates: list[tuple[Listing, LandedCost]]) -> list[RankedPick]:
        plan = parse_query(req.query)
        scored = [(l, c, p) for l, c, p in score_parts(req, prefs, candidates) if not p.drop]
        scored.sort(key=lambda t: (t[2].total, -t[1].total_usd), reverse=True)
        picks = []
        for i, (l, c, p) in enumerate(scored):
            nxt = scored[i + 1][1].total_usd if i + 1 < len(scored) else None
            matched = relevance(plan, l).matched
            picks.append(RankedPick(l, c, p.total, _reason(req, l, c, p, i, nxt, matched)))
        return picks
