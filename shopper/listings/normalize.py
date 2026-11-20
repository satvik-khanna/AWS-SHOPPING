"""Raw marketplace row (Japanese title / description / condition text) -> models.Listing.

Raw row keys: id, source, url, title_ja, price_jpy, seller, and optionally description_ja,
condition_ja, ships_abroad, photos. Optional overrides win over extraction: title_en, condition,
weight_kg, category, fields.
"""
from __future__ import annotations

import re
import unicodedata

from ..models import Listing
from .lexicon import (ACCESSORY_ALWAYS, ACCESSORY_NOUNS, CITY_POP_ARTISTS, CONDITION_TABLE,
                      DEFAULT_WEIGHT_KG, ENTRIES, FLAWS, PHRASES)
from .query import has_japanese

CONDITIONS = ["junk", "fair", "good", "like_new", "new"]


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


# ---------------------------------------------------------------- condition
_COND = sorted(CONDITION_TABLE, key=lambda kv: len(kv[0]), reverse=True)


def _lookup_condition(text: str) -> str | None:
    t = re.sub(r"[\s、,・]", "", nfkc(text))
    best = None                     # earliest match wins, longest phrase on ties
    for phrase, cond in _COND:
        i = t.find(phrase)
        if i < 0:
            continue
        if cond == "junk":          # junk wording anywhere beats every other grade word
            return "junk"
        if best is None or i < best[0]:
            best = (i, cond)
    return best[1] if best else None


def normalize_condition(condition_ja: str | None, title_ja: str = "") -> tuple[str, str]:
    """-> (normalized condition, where it came from: 'grade' | 'title' | 'assumed').

    The marketplace grade (e.g. 目立った傷や汚れなし -> good) is used when present, but a title that
    says ジャンク always wins: sellers often grade junk items as merely 'scratched'.
    """
    title_cond = _lookup_condition(title_ja)
    if title_cond == "junk":
        return "junk", "title"
    if condition_ja:
        c = condition_ja if condition_ja in CONDITIONS else _lookup_condition(condition_ja)
        if c:
            return c, "grade"
    if title_cond:
        return title_cond, "title"
    return "good", "assumed"


# ---------------------------------------------------------------- fields
_NEG = re.compile(r"な[しい]|無し|ありません|ございません|見当たりません|見られません|吸いません|吸わな")
_POS = re.compile(r"あり(?!ません)|有り|ござい(?!ません)")
_FLAWS = sorted(FLAWS, key=lambda kv: len(kv[0]), reverse=True)
_GRADE = r"(M-|NM|EX\+|EX-|EX|VG\+\+|VG\+|VG|G\+|G|S|A|B|C)"
_ACC_NOUN = re.compile("(?:" + "|".join(map(re.escape, ACCESSORY_NOUNS)) + r")(?!付|あり|有|込|セット|\s*付属)")
_ACC_MARK = re.compile(r"専用|(?<![使未利])用(?![途品])|対応|単品|のみ")
_COLORS = sorted(((ja, en) for en, kind, jas, _ in ENTRIES if kind == "color" for ja in jas),
                 key=lambda p: len(p[0]), reverse=True)


def _flaws(text: str) -> list[str]:
    out: list[str] = []
    t = text
    for ja, en in _FLAWS:
        start = 0
        while True:
            i = t.find(ja, start)
            if i < 0:
                break
            start = i + len(ja)
            window = re.split(r"[。\n!!]", t[start:start + 16])[0]
            neg, pos = _NEG.search(window), _POS.search(window)
            negated = bool(neg) and (not pos or neg.start() < pos.start())
            t = t[:i] + "＿" * len(ja) + t[start:]          # consume so 傷 does not re-match 擦り傷
            if not negated and en not in out:
                out.append(en)
    return out


def is_accessory(title_ja: str) -> bool:
    t = nfkc(title_ja)
    if any(w in t for w in ACCESSORY_ALWAYS):
        return True
    return bool(_ACC_NOUN.search(t)) and bool(_ACC_MARK.search(t))


def extract_fields(title_ja: str, description_ja: str = "") -> dict[str, str]:
    """Pull structured specs out of Japanese listing text. Title wins over description."""
    title, desc = nfkc(title_ja), nfkc(description_ja)
    both = title + "\n" + desc
    f: dict[str, str] = {}

    if is_accessory(title):
        f["item_type"] = "accessory"
    for ja, en in _COLORS:
        if ja in title:
            f["color"] = en
            break
    m = re.search(r"シャッター(?:回数|数|カウント)[^\d\n]{0,6}?([\d,]{2,7})", both)
    if m:
        f["shutter_count"] = m.group(1).replace(",", "")
    if re.search(r"箱(?:なし|無し|無)|箱はありません", both):
        f["box"] = "no"
    elif re.search(r"元箱|箱付|箱あり|箱有|外箱", both) and "item_type" not in f:
        f["box"] = "yes"
    m = re.search(r"(?<![\d.])(2[0-9]|3[0-2])(\.\d)?\s*cm", both, re.I)
    if m:
        f["size_cm"] = m.group(1) + (m.group(2) or "")
    if re.search(r"帯(?:なし|無し|無)", both):
        f["obi"] = "no"
    elif re.search(r"帯付|帯あり|帯有", both):
        f["obi"] = "yes"
    if re.search(r"未開封|シュリンク付", both):
        f["sealed"] = "yes"
    elif re.search(r"開封済|開封品", both):
        f["sealed"] = "no"
    m = re.search(r"盤質?\s*[::]?\s*" + _GRADE + r"(?![A-Za-z])", both)
    if m:
        f["media_grade"] = m.group(1)
    m = re.search(r"ジャケット?\s*[::]?\s*" + _GRADE + r"(?![A-Za-z])", both)
    if m:
        f["sleeve_grade"] = m.group(1)
    if "オリジナル盤" in both or "初版" in both:
        f["edition"] = "original"
    elif re.search(r"再発|復刻|リイシュー", both):
        f["edition"] = "reissue"
    m = re.search(r"(19[5-9]\d|20[0-2]\d)年", both)
    if m:
        f["year"] = m.group(1)
    m = re.search(r"(?<![A-Za-z0-9])([A-Z]{2,5}-\d{3,6})(?![A-Za-z0-9])", title)
    if m:
        f["catalog_no"] = m.group(1)
    m = re.search(r"(日本|USA|アメリカ|イギリス|英国|ドイツ)製", both)
    if m:
        f["made_in"] = {"日本": "Japan", "USA": "USA", "アメリカ": "USA", "イギリス": "UK",
                        "英国": "UK", "ドイツ": "Germany"}[m.group(1)]
    m = re.search(r"(?<!\d)1/(\d{1,3})(?!\d)", title)
    if m:
        f["scale"] = "1/" + m.group(1)
    if re.search(r"動作確認済|動作品|動作良好|動作問題な", both):
        f["tested"] = "working"
    if "シティポップ" in both or "シティ・ポップ" in both or any(a in both for a in CITY_POP_ARTISTS):
        f["genre"] = "city pop"
    elif re.search(r"サウンドトラック|サントラ", both):
        f["genre"] = "soundtrack"
    if re.search(r"送料込|送料無料|送料出品者負担", both):
        f["domestic_shipping"] = "seller pays"
    elif re.search(r"着払い|送料別|送料落札者負担", both):
        f["domestic_shipping"] = "buyer pays"
    flaws = _flaws(both)
    if flaws:
        f["flaws"] = ", ".join(flaws)
    return f


# ---------------------------------------------------------------- category / weight
_CATEGORY_RULES: list[tuple[str, str]] = [
    ("sneakers", r"スニーカー|ナイキ|nike|ダンク|ジョーダン|ニューバランス|new balance|adidas|アディダス|オニツカ"),
    ("vinyl", r"レコード|(?<![a-z])lp(?![a-z])|帯付|帯なし|ep盤|12インチ|7インチ|アナログ盤"),
    ("figure", r"フィギュア|ねんどろいど|figma|figuarts|フィギュアーツ|一番くじ|プラモデル|メタルビルド|ポップアップパレード"),
    ("camera", r"カメラ|富士フイルム|富士フィルム|fujifilm|x100|ricoh|リコー|contax|コンタックス|nikon|ニコン|canon|キヤノン|leica|ライカ|シャッター|一眼"),
]


def guess_category(title_ja: str, description_ja: str = "") -> str | None:
    title = nfkc(title_ja).lower()
    cat = None
    for name, pat in _CATEGORY_RULES:
        if re.search(pat, title):
            cat = name
            break
    if cat is None:
        both = title + " " + nfkc(description_ja).lower()
        for name, pat in _CATEGORY_RULES:
            if re.search(pat, both):
                cat = name
                break
    if is_accessory(title_ja):
        if re.search(r"バッテリー|電池", title):
            return "battery_loose"          # loose lithium cells: not shippable by air
        if cat == "camera":
            return "camera_accessory"
    return cat


# ---------------------------------------------------------------- title translation
def _glossary() -> list[tuple[str, str]]:
    pairs = [(nfkc(ja), en) for en, _k, jas, _e in ENTRIES for ja in jas] + [(nfkc(ja), en) for ja, en in PHRASES]
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


_GLOSS = _glossary()
_GLOSS_MAP = {}
for _ja, _en in _GLOSS:
    _GLOSS_MAP.setdefault(_ja, _en)
_GLOSS_RE = re.compile("|".join(re.escape(ja) for ja, _ in _GLOSS))      # longest alternative first
_DECOR = re.compile(r"[★☆◆◇■□●○◎♪※〇]+|[【】《》「」『』\[\]]")
_JA_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def _translate_chunk(s: str) -> str:
    t = _GLOSS_RE.sub(lambda m: " " + _GLOSS_MAP[m.group(0)] + " ", s).replace("年", " ")
    t = re.sub(r"\s*[、,・/]\s*$", "", re.sub(r"\s+", " ", t)).strip()
    return re.sub(r"\s+([,.])", r"\1", t)


def translate_title(title_ja: str) -> str:
    """Glossary translation JA -> EN. Unknown Japanese words are kept as they are."""
    t = _DECOR.sub(" ", nfkc(title_ja))
    t = re.sub(r'[“”"]', '"', t)
    m = re.match(r"^(.*?[A-Za-z0-9])\s*(?:専用|用)\s+(.+)$", t)        # "X100V 用 予備バッテリー" -> "... for X100V"
    if m:
        return f"{_translate_chunk(m.group(2))} for {_translate_chunk(m.group(1))}"
    return _translate_chunk(t)


def untranslated(title_en: str) -> bool:
    return has_japanese(title_en)


# ---------------------------------------------------------------- raw -> Listing
_BUYEE = {"mercari": "https://buyee.jp/mercari/item/{id}",
          "yahoo_auctions": "https://buyee.jp/item/jdirectitems/auction/{id}"}


def to_listing(raw: dict) -> Listing:
    title = raw["title_ja"]
    desc = raw.get("description_ja", "")
    cond, cond_src = normalize_condition(raw.get("condition") or raw.get("condition_ja"), title)
    fields = extract_fields(title, desc)
    if raw.get("condition_ja"):
        fields["condition_ja"] = raw["condition_ja"]
    if cond_src == "assumed":
        fields["condition_source"] = "not stated, assumed good"
    elif cond_src == "title":
        fields["condition_source"] = "from title wording"
    category = raw.get("category") or guess_category(title, desc)
    weight = raw.get("weight_kg")
    if weight is None:
        weight = DEFAULT_WEIGHT_KG.get(category or "")
    source = raw.get("source", "other")
    native = raw["id"].split(":", 1)[-1]
    if source in _BUYEE:
        fields.setdefault("buyee_url", _BUYEE[source].format(id=native))
    fields.update({k: str(v) for k, v in (raw.get("fields") or {}).items()})
    return Listing(
        id=raw["id"], source=source, url=raw["url"], title_ja=title,
        title_en=raw.get("title_en") or translate_title(title),
        price_jpy=int(raw["price_jpy"]), condition=cond, seller=raw.get("seller") or "unknown",
        ships_abroad=bool(raw.get("ships_abroad", False)), weight_kg=weight, category=category,
        fields=fields, photos=list(raw.get("photos") or []),
    )
