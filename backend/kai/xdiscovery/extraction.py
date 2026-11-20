"""Turn a raw post into a SocialProductLead.

Rule-based extraction always runs. When an LLM is configured it adds a real
translation and fills fields the rules missed; each LLM value is checked
against the original text before it is accepted. Anything not found stays
None ("unknown"). The original Japanese text is always preserved."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..llm import LLMClient
from ..models import AnnouncementType, ExternalLink, Provenance, SocialProductLead, new_id
from ..security import UNTRUSTED_SYSTEM_RULE, detect_injection, sanitize_untrusted, wrap_untrusted
from ..textutil import nfkc, strip_urls
from ..vocab import ANNOUNCEMENT_TERMS, CATEGORIES, detect_attributes, detect_category, glossary_gloss
from .providers import RawPost
from .storefront import annotate_link

JST = timezone(timedelta(hours=9))

_SOLD_OUT_DONE = re.compile(r"完売(しました|致しました|いたしました|となりました|です|御礼)|売り切れ(ました|です)|SOLD\s?OUT|品切れ", re.I)
_RESTOCK = re.compile(r"再販|再入荷|再販売|入荷しました|入荷")
_TYPE_RULES: list[tuple[AnnouncementType, re.Pattern[str]]] = [
    (AnnouncementType.LOTTERY, re.compile(r"抽選")),
    (AnnouncementType.PREORDER, re.compile(r"予約|受注生産|受注販売|受注開始|プレオーダー|pre-?order", re.I)),
    (AnnouncementType.RESTOCK, _RESTOCK),
    (AnnouncementType.NEW_RELEASE, re.compile(r"新作|新商品|新発売|発売開始|販売開始|販売します|発売|リリース|new release", re.I)),
    (AnnouncementType.HANDMADE, re.compile(r"ハンドメイド|手作り|手づくり|一点物|作家")),
    (AnnouncementType.GENERAL_PROMOTION, re.compile(r"通販|販売中|セール|SALE|オンラインショップ|ショップ|出品|お知らせ")),
]
_CLAIM = {
    AnnouncementType.SOLD_OUT: "Post says it sold out",
    AnnouncementType.LOTTERY: "Post describes a lottery sale",
    AnnouncementType.PREORDER: "Post describes preorders / made to order",
    AnnouncementType.RESTOCK: "Post says it was restocked",
    AnnouncementType.NEW_RELEASE: "Post says a new product is on sale or coming",
}

_PRICE = re.compile(r"[¥]\s*([0-9][0-9,]*)|([0-9][0-9,]*)\s*円")
_DATE = re.compile(
    r"(?:(?P<y>20\d{2})\s*[年/.]\s*)?(?P<m>\d{1,2})\s*[月/]\s*(?P<d>\d{1,2})\s*日?"
    r"(?:\s*[（(][月火水木金土日祝・]{1,3}[)）])?"
    r"(?:\s*(?P<h>\d{1,2})\s*[:時]\s*(?P<min>\d{2})?分?)?"
)
_DEADLINE_AFTER = re.compile(r"^\s*(まで|迄|締切|〆切|締め切り|〆)")
_DEADLINE_BEFORE = re.compile(r"(締切|〆切|締め切り|期限|受付終了)[^0-9]{0,6}$")
_START_AFTER = re.compile(r"^\s*(から|より|〜|~|-|発売|販売開始|開始|予約開始|受付開始|スタート|に発売|に販売)")
_START_BEFORE = re.compile(r"(発売日|販売開始|発売|リリース|販売日|開始)[^0-9]{0,6}$")
_BRACKETS = re.compile(r"[「『]([^」』\n]{2,40})[」』]")
_SQUARE = re.compile(r"【([^】\n]{2,40})】")


def classify_announcement(text: str) -> AnnouncementType:
    t = nfkc(text)
    if _SOLD_OUT_DONE.search(t) and not _RESTOCK.search(t):
        return AnnouncementType.SOLD_OUT
    for kind, pat in _TYPE_RULES:
        if pat.search(t):
            return kind
    return AnnouncementType.UNKNOWN


def extract_price_jpy(text: str) -> tuple[int | None, str | None]:
    """Return (price, note). Shipping fees are skipped. Unknown stays None."""
    t = nfkc(text)
    prices: list[int] = []
    for m in _PRICE.finditer(t):
        if "送料" in t[max(0, m.start() - 6): m.start()]:
            continue
        raw = (m.group(1) or m.group(2)).replace(",", "")
        if raw.isdigit() and 100 <= int(raw) <= 10_000_000 and int(raw) not in prices:
            prices.append(int(raw))
    if not prices:
        return None, None
    note = f"Post lists several prices ({', '.join(f'¥{p:,}' for p in prices)}); the first is used." if len(prices) > 1 else None
    return prices[0], note


def _to_iso(m: re.Match[str], base: datetime) -> str | None:
    try:
        month, day = int(m.group("m")), int(m.group("d"))
        year = int(m.group("y")) if m.group("y") else base.year
        hour = int(m.group("h")) if m.group("h") else None
        minute = int(m.group("min")) if m.group("min") else 0
        if hour is not None:
            if hour == 24:
                hour, minute = 23, 59
            dt = datetime(year, month, day, hour, minute, tzinfo=JST)
        else:
            dt = datetime(year, month, day, tzinfo=JST)
        if not m.group("y") and dt < base.astimezone(JST) - timedelta(days=60):
            dt = dt.replace(year=year + 1)  # a bare "1/5" posted in December means next year
        return dt.isoformat(timespec="minutes") if hour is not None else dt.date().isoformat()
    except ValueError:
        return None


def extract_dates(text: str, base: datetime) -> dict[str, Any]:
    """Classify dates by nearby wording. Dates with an unclear role are
    returned under 'unclassified' rather than guessed."""
    t = nfkc(text)
    out: dict[str, Any] = {"deadline": None, "start": None, "unclassified": []}
    for m in _DATE.finditer(t):
        iso = _to_iso(m, base)
        if not iso:
            continue
        before, after = t[max(0, m.start() - 14): m.start()], t[m.end(): m.end() + 14]
        if re.match(r"\s*(スケール|scale|サイズ|size)", after, re.I):
            continue  # "1/7スケール" is a figure scale, not a date
        if _DEADLINE_AFTER.search(after) or _DEADLINE_BEFORE.search(before):
            out["deadline"] = out["deadline"] or iso
        elif _START_AFTER.search(after) or _START_BEFORE.search(before):
            out["start"] = out["start"] or iso
        else:
            out["unclassified"].append(iso)
    return out


def extract_product_name(text: str) -> str | None:
    t = nfkc(strip_urls(text))
    generic = set(ANNOUNCEMENT_TERMS) | {"お知らせ", "告知", "新作", "予約", "再販", "限定"}
    for pat in (_BRACKETS, _SQUARE):
        for m in pat.finditer(t):
            name = m.group(1).strip()
            if name in generic or any(name == g or name.replace("のお知らせ", "") == g for g in generic):
                continue
            if pat is _SQUARE and any(g in name for g in ("お知らせ", "告知", "情報", "予約", "新作", "再販", "抽選", "完売")):
                continue
            return name
    return None


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


_LLM_SYSTEM = (
    UNTRUSTED_SYSTEM_RULE + " You analyze Japanese social media posts about products. Respond with one JSON object "
    "and nothing else, with keys: englishTranslation (faithful English translation of the whole post), "
    "isProductAnnouncement (boolean), productName (the product name exactly as written in the post, or null), "
    "productCategory (short English noun phrase, or null), announcementType (one of NEW_RELEASE, RESTOCK, PREORDER, "
    "LOTTERY, HANDMADE, SOLD_OUT, GENERAL_PROMOTION, UNKNOWN), announcedPriceJPY (integer or null), "
    "releaseDate, preorderDeadline, lotteryDeadline (ISO 8601 dates or null). Use null for anything the post does "
    "not state. Do not infer prices, dates or availability."
)


class LeadExtractor:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def extract(self, post: RawPost, requested_category: str | None = None,
                      run_id: str | None = None) -> SocialProductLead:
        text = sanitize_untrusted(post.text, 4000)
        base = _parse_ts(post.created_at) or datetime.now(timezone.utc)
        notes: list[str] = []

        kind = classify_announcement(text)
        price, price_note = extract_price_jpy(text)
        if price_note:
            notes.append(price_note)
        dates = extract_dates(text, base)
        name = extract_product_name(text)
        category = detect_category(text) or post.category_hint
        if dates["unclassified"]:
            notes.append("Date(s) in the post with an unclear role were not assigned: " + ", ".join(dates["unclassified"]))

        translation: str | None = None
        translation_method = "none"
        extraction_method = "heuristic"
        if post.fixture_translation:
            translation, translation_method = post.fixture_translation, "fixture_provided"

        llm_says_not_product = False
        if self.llm.available:
            data = await self.llm.complete_json(
                _LLM_SYSTEM,
                f"Post time: {post.created_at or 'unknown'}\n" + wrap_untrusted(text, "x_post"),
                purpose="post_extract", run_id=run_id)
            if data:
                extraction_method = "llm+heuristic"
                tr = data.get("englishTranslation")
                if isinstance(tr, str) and tr.strip():
                    translation, translation_method = sanitize_untrusted(tr.strip(), 2000), "llm"
                llm_says_not_product = data.get("isProductAnnouncement") is False
                # Accept LLM values only when they are grounded in the post text.
                norm = nfkc(text)
                llm_name = data.get("productName")
                if not name and isinstance(llm_name, str) and llm_name.strip() and nfkc(llm_name.strip()) in norm:
                    name = llm_name.strip()[:80]
                llm_price = data.get("announcedPriceJPY")
                if price is None and isinstance(llm_price, (int, float)) and llm_price > 0:
                    digits = norm.replace(",", "")
                    if str(int(llm_price)) in digits:
                        price = int(llm_price)
                    else:
                        notes.append("A price suggested by the model was discarded because it does not appear in the post.")
                llm_type = data.get("announcementType")
                if kind == AnnouncementType.UNKNOWN and llm_type in AnnouncementType.__members__:
                    kind = AnnouncementType(llm_type)
                if not category and isinstance(data.get("productCategory"), str):
                    category = detect_category(data["productCategory"])

        glossary = glossary_gloss(text)
        if translation is None:
            translation_method = "glossary_gloss" if glossary else "none"

        lottery_deadline = preorder_deadline = None
        if dates["deadline"]:
            if kind == AnnouncementType.LOTTERY:
                lottery_deadline = dates["deadline"]
            elif kind == AnnouncementType.PREORDER:
                preorder_deadline = dates["deadline"]
            else:
                notes.append(f"Deadline mentioned in the post: {dates['deadline']}")

        links: list[ExternalLink] = [annotate_link(l.model_copy()) for l in post.links]
        store_links = [l for l in links if l.kind in ("STOREFRONT", "LINK_HUB", "OTHER")]
        attributes = detect_attributes(text)
        flags = detect_injection(post.text)

        # ---- usefulness ---------------------------------------------------
        useful, reason = True, None
        cat_terms = []
        if requested_category in CATEGORIES:
            cat_terms = [t.lower() for t in CATEGORIES[requested_category]["ja"] + CATEGORIES[requested_category]["en"]]
        lowered = nfkc(text).lower()
        if kind == AnnouncementType.UNKNOWN and not store_links:
            useful, reason = False, "No announcement wording and no store link."
        elif llm_says_not_product and kind in (AnnouncementType.UNKNOWN, AnnouncementType.GENERAL_PROMOTION):
            useful, reason = False, "Model judged this is not a product announcement."
        elif cat_terms and not post.partial_text and not any(t in lowered for t in cat_terms):
            useful, reason = False, f"Does not mention the requested category ({CATEGORIES[requested_category]['label']})."

        confidence = 0.2
        confidence += 0.25 if kind != AnnouncementType.UNKNOWN else 0
        confidence += 0.15 if name else 0
        confidence += 0.10 if price is not None else 0
        confidence += 0.15 if store_links else 0
        confidence += 0.10 if category else 0
        confidence += 0.05 if extraction_method.startswith("llm") else 0
        confidence -= 0.20 if post.partial_text else 0
        confidence = round(max(0.05, min(0.95, confidence)), 2)

        if post.partial_text:
            notes.append("Text is a search-engine snippet and may be truncated.")
        handle = post.author_handle
        return SocialProductLead(
            id=new_id("lead"), source_post_url=post.url, post_id=post.post_id, author_handle=handle,
            author_display_name=post.author_name,
            # fixture accounts do not exist, so they never get a real x.com profile link
            author_profile_url=(post.url.split("/status/")[0] if post.is_fixture
                                else f"https://x.com/{handle}") if handle else None,
            original_japanese_text=text, english_translation=translation, product_name=name,
            product_category=category, announcement_type=kind, announced_price_jpy=price,
            release_date=dates["start"], preorder_deadline=preorder_deadline, lottery_deadline=lottery_deadline,
            external_links=links, media_urls=post.media_urls, confidence=confidence,
            source_timestamp=post.created_at, retrieval_timestamp=post.retrieved_at,
            announced_availability_claim=_CLAIM.get(kind),
            provenance=Provenance(provider=post.provider, is_fixture=post.is_fixture, query=post.query,
                                  coverage_note=post.coverage_note, partial_text=post.partial_text,
                                  from_cache=post.from_cache, extraction_method=extraction_method,
                                  translation_method=translation_method, notes=notes),
            glossary=glossary, attributes=attributes, useful=useful, usefulness_reason=reason, injection_flags=flags,
        )
