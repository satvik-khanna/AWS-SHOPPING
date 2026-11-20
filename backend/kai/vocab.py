"""Japanese shopping vocabulary: categories, announcement terms, attributes."""

from __future__ import annotations

from .textutil import nfkc

# category key -> label, Japanese search terms (first is primary), English triggers
CATEGORIES: dict[str, dict] = {
    "keycaps": {
        "label": "Artisan keycaps",
        "ja": ["キーキャップ", "アルチザンキーキャップ", "自作キーボード"],
        "en": ["keycap", "keycaps", "artisan key", "mechanical keyboard", "keyboard"],
    },
    "figures": {
        "label": "Figures",
        "ja": ["フィギュア", "ガレージキット", "ソフビ"],
        "en": ["figure", "figures", "figurine", "garage kit", "sofubi", "soft vinyl"],
    },
    "cameras": {
        "label": "Cameras",
        "ja": ["カメラ", "フィルムカメラ", "レンズ"],
        "en": ["camera", "cameras", "film camera", "lens"],
    },
    "handmade": {
        "label": "Handmade goods",
        "ja": ["ハンドメイド", "作家もの", "一点物"],
        "en": ["handmade", "handcrafted", "craft", "crafts"],
    },
    "stationery": {
        "label": "Stationery",
        "ja": ["文房具", "万年筆", "ノート"],
        "en": ["stationery", "fountain pen", "notebook", "pen"],
    },
    "fashion": {
        "label": "Fashion",
        "ja": ["古着", "スニーカー", "アクセサリー"],
        "en": ["fashion", "vintage clothing", "sneakers", "accessory", "accessories", "jewelry"],
    },
}

# Announcement vocabulary -> English gloss.
ANNOUNCEMENT_TERMS: dict[str, str] = {
    "新作": "new product",
    "新商品": "new product",
    "新発売": "newly released",
    "販売開始": "sales started",
    "発売開始": "release started",
    "発売": "release",
    "予約開始": "preorders opened",
    "予約受付": "accepting preorders",
    "予約": "preorder",
    "抽選販売": "lottery sale",
    "抽選": "lottery",
    "再販": "restock / re-release",
    "再入荷": "restocked",
    "入荷": "arrived / in stock",
    "数量限定": "limited quantity",
    "限定": "limited",
    "受注生産": "made to order",
    "受注販売": "made-to-order sale",
    "完売": "sold out",
    "売り切れ": "sold out",
    "品切れ": "out of stock",
    "通販": "online sale",
    "先着": "first come, first served",
    "締切": "deadline",
    "まで": "until",
    "税込": "tax included",
    "送料": "shipping fee",
    "ハンドメイド": "handmade",
    "一点物": "one of a kind",
    "お知らせ": "announcement",
}

# Query intents. Each intent is a separate search so results are not mixed.
INTENTS: dict[str, dict] = {
    "release": {"terms": ["新作", "限定", "再販"], "suffix": "lang:ja -is:retweet", "label": "new / limited / restock"},
    "preorder_lottery": {"terms": ["予約開始", "抽選販売", "受注生産"], "suffix": "lang:ja -is:retweet", "label": "preorder / lottery / made to order"},
    "on_sale": {"terms": ["販売開始", "通販", "入荷"], "suffix": "lang:ja has:links -is:retweet", "label": "on sale now, with store link"},
    "limited_firstcome": {"terms": ["数量限定", "先着"], "suffix": "lang:ja has:links -is:retweet", "label": "limited quantity / first come"},
}

# Product attributes used for preference matching: key -> (label, terms in JA/EN)
ATTRIBUTES: dict[str, dict] = {
    "handmade": {"label": "handmade", "terms": ["ハンドメイド", "手作り", "手づくり", "手作業", "一点物", "アルチザン", "handmade", "hand-made", "handcrafted", "artisan"]},
    "matte": {"label": "matte finish", "terms": ["マット", "艶消し", "つや消し", "ツヤ消し", "matte", "matt finish"]},
    "glossy": {"label": "glossy finish", "terms": ["光沢", "グロス", "ツヤあり", "艶あり", "glossy", "gloss finish"]},
    "limited": {"label": "limited run", "terms": ["数量限定", "限定", "limited edition", "limited run", "limited"]},
    "resin": {"label": "resin", "terms": ["レジン", "樹脂", "resin"]},
    "ceramic": {"label": "ceramic", "terms": ["陶器", "陶芸", "セラミック", "磁器", "ceramic"]},
    "wood": {"label": "wood", "terms": ["木製", "ウッド", "木工", "wooden", "wood"]},
    "metal": {"label": "metal", "terms": ["真鍮", "アルミ", "金属", "メタル", "brass", "aluminum", "aluminium", "metal"]},
    "translucent": {"label": "translucent", "terms": ["クリア", "透明", "半透明", "translucent", "clear"]},
    "mass_produced": {"label": "mass-produced", "terms": ["量産", "大量生産", "mass-produced", "mass produced"]},
    "used": {"label": "used / second-hand", "terms": ["中古", "ジャンク", "used", "second-hand", "secondhand"]},
    "minimal": {"label": "minimal design", "terms": ["シンプル", "ミニマル", "minimal", "minimalist"]},
}


def detect_attributes(text: str | None) -> list[str]:
    t = nfkc(text or "").lower()
    found = []
    for key, spec in ATTRIBUTES.items():
        if any(term.lower() in t for term in spec["terms"]):
            found.append(key)
    return found


def detect_category(text: str | None) -> str | None:
    t = nfkc(text or "").lower()
    best: tuple[int, str] | None = None
    for key, spec in CATEGORIES.items():
        hits = sum(1 for term in spec["ja"] if term.lower() in t) + sum(1 for term in spec["en"] if term in t)
        if hits and (best is None or hits > best[0]):
            best = (hits, key)
    return best[1] if best else None


def glossary_gloss(text: str | None) -> list[str]:
    """English glosses for announcement terms found in the text. This is a
    keyword gloss shown when no LLM is available; it is not a translation."""
    t = nfkc(text or "")
    seen: list[str] = []
    for term, gloss in sorted(ANNOUNCEMENT_TERMS.items(), key=lambda kv: -len(kv[0])):
        if term in t:
            entry = f"{term} = {gloss}"
            # skip shorter terms already covered by a longer one (e.g. 予約 inside 予約開始)
            if not any(term in s.split(" = ")[0] and s != entry for s in seen):
                seen.append(entry)
    return seen
