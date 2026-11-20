"""Small hand-built EN<->JA lexicon. One table feeds three things:
query expansion (EN -> JA keywords), title translation (JA -> EN) and relevance matching.

Offline stand-in for Superlinked SIE translation: it only knows the words below, anything
else in a Japanese title is passed through untranslated.
"""
from __future__ import annotations

# (display_en, kind, ja_forms, extra_en_forms)
# kind: brand | model | name (artist / franchise / nickname) | type | term | color
ENTRIES: list[tuple[str, str, list[str], list[str]]] = [
    # --- camera brands
    ("Fujifilm", "brand", ["富士フイルム", "富士フィルム", "フジフイルム", "フジフィルム"], ["fujifilm", "fuji film", "fuji"]),
    ("Ricoh", "brand", ["リコー"], ["ricoh"]),
    ("Contax", "brand", ["コンタックス"], ["contax"]),
    ("Nikon", "brand", ["ニコン"], ["nikon"]),
    ("Canon", "brand", ["キヤノン", "キャノン"], ["canon"]),
    ("Leica", "brand", ["ライカ"], ["leica"]),
    ("Olympus", "brand", ["オリンパス"], ["olympus"]),
    ("Pentax", "brand", ["ペンタックス"], ["pentax"]),
    # --- sneaker brands / models
    ("Nike", "brand", ["ナイキ"], ["nike"]),
    ("New Balance", "brand", ["ニューバランス"], ["new balance"]),
    ("adidas", "brand", ["アディダス"], ["adidas"]),
    ("Onitsuka Tiger", "brand", ["オニツカタイガー", "オニツカ"], ["onitsuka tiger", "onitsuka"]),
    ("Dunk Low", "model", ["ダンク ロー", "ダンクロー", "ダンク LOW"], ["dunk low", "dunk"]),
    ("Air Jordan 1", "model", ["エアジョーダン1", "エア ジョーダン 1", "ジョーダン1"], ["air jordan 1", "jordan 1", "aj1"]),
    ("Mexico 66", "model", ["メキシコ66", "メキシコ 66"], ["mexico 66"]),
    ("Samba", "model", ["サンバ"], ["samba"]),
    ("GR III", "model", [], ["gr iii", "griii", "gr3"]),
    ("GR IIIx", "model", [], ["gr iiix", "griiix", "gr3x"]),
    ("Panda", "name", ["パンダ"], ["panda"]),
    ("Chicago", "name", ["シカゴ"], ["chicago"]),
    # --- figure lines
    ("Nendoroid", "brand", ["ねんどろいど"], ["nendoroid"]),
    ("figma", "brand", ["フィグマ"], ["figma"]),
    ("S.H.Figuarts", "brand", ["S.H.フィギュアーツ", "フィギュアーツ"], ["s.h.figuarts", "sh figuarts", "figuarts"]),
    ("Ichiban Kuji", "brand", ["一番くじ"], ["ichiban kuji"]),
    ("Pop Up Parade", "brand", ["ポップアップパレード"], ["pop up parade"]),
    ("Metal Build", "brand", ["メタルビルド"], ["metal build"]),
    # --- franchises / characters
    ("Hatsune Miku", "name", ["初音ミク"], ["hatsune miku", "miku"]),
    ("Son Goku", "name", ["孫悟空"], ["son goku", "goku"]),
    ("Dragon Ball Z", "name", ["ドラゴンボールZ"], ["dragon ball z", "dbz"]),
    ("Dragon Ball", "name", ["ドラゴンボール"], ["dragon ball", "dragonball"]),
    ("One Piece", "name", ["ワンピース"], ["one piece"]),
    ("Luffy", "name", ["ルフィ"], ["luffy"]),
    ("Gear 5", "name", ["ギア5"], ["gear 5", "gear5"]),
    ("Evangelion Unit-01", "name", ["エヴァンゲリオン初号機", "エヴァンゲリオン 初号機", "エヴァ初号機"], ["evangelion unit-01", "eva unit 01", "eva-01", "unit-01", "unit 01"]),
    ("Evangelion", "name", ["エヴァンゲリオン", "エヴァ"], ["evangelion", "eva"]),
    ("Saber", "name", ["セイバー"], ["saber"]),
    ("Chainsaw Man", "name", ["チェンソーマン"], ["chainsaw man"]),
    ("Power", "name", ["パワー"], ["power"]),
    ("Tanjiro Kamado", "name", ["竈門炭治郎", "炭治郎"], ["tanjiro kamado", "tanjiro"]),
    ("Demon Slayer", "name", ["鬼滅の刃"], ["demon slayer", "kimetsu no yaiba", "kimetsu"]),
    # --- artists / albums
    ("Tatsuro Yamashita", "name", ["山下達郎"], ["tatsuro yamashita", "yamashita tatsuro"]),
    ("Mariya Takeuchi", "name", ["竹内まりや"], ["mariya takeuchi", "takeuchi mariya"]),
    ("Taeko Onuki", "name", ["大貫妙子"], ["taeko onuki", "taeko ohnuki", "onuki taeko"]),
    ("Miki Matsubara", "name", ["松原みき"], ["miki matsubara", "matsubara miki"]),
    ("Anri", "name", ["杏里"], ["anri"]),
    ("Joe Hisaishi", "name", ["久石譲"], ["joe hisaishi", "hisaishi"]),
    ("Haruomi Hosono", "name", ["細野晴臣"], ["haruomi hosono", "hosono haruomi"]),
    ("Nausicaa of the Valley of the Wind", "name", ["風の谷のナウシカ"], ["nausicaa of the valley of the wind", "nausicaa"]),
    ("Plastic Love", "name", ["プラスティック・ラヴ", "プラスティックラヴ", "プラスティック・ラブ"], ["plastic love"]),
    ("Mayonaka no Door (Stay With Me)", "name", ["真夜中のドア"], ["mayonaka no door", "stay with me"]),
    # --- item types / genres
    ("film camera", "type", ["フィルムカメラ"], ["film camera"]),
    ("camera", "type", ["カメラ"], ["camera", "cameras"]),
    ("sneakers", "type", ["スニーカー"], ["sneakers", "sneaker", "shoes", "kicks"]),
    ("vinyl", "type", ["レコード", "アナログ盤"], ["vinyl", "record", "records", "lp"]),
    ("model kit", "type", ["プラモデル", "プラモ"], ["model kit", "plastic model", "gunpla"]),
    ("figure", "type", ["フィギュア"], ["figure", "figures", "figurine"]),
    ("spare battery", "type", ["予備バッテリー"], ["spare battery"]),
    ("battery", "type", ["バッテリー", "電池"], ["battery", "batteries"]),
    ("leather case", "type", ["レザーケース"], ["leather case"]),
    ("case", "type", ["ケース"], ["case"]),
    ("empty box", "type", ["空箱のみ", "空箱", "箱のみ"], ["empty box", "box only"]),
    ("soundtrack", "term", ["サウンドトラック", "サントラ"], ["soundtrack", "ost"]),
    ("city pop", "term", ["シティポップ", "シティ・ポップ"], ["city pop", "citypop"]),
    # --- colors
    ("titanium silver", "color", ["チタンシルバー"], ["titanium silver"]),
    ("silver", "color", ["シルバー"], ["silver"]),
    ("black", "color", ["ブラック"], ["black"]),
    ("white", "color", ["ホワイト"], ["white"]),
    ("gray", "color", ["グレー", "グレイ"], ["gray", "grey"]),
    ("yellow", "color", ["イエロー"], ["yellow"]),
    ("red", "color", ["レッド"], ["red"]),
    ("blue", "color", ["ブルー"], ["blue"]),
]

# Japanese phrases that only matter for translating titles (not searchable concepts).
PHRASES: list[tuple[str, str]] = [
    ("新品未使用", "new, unused"), ("新品未開封", "new, sealed"), ("新品、未使用", "new, unused"),
    ("未使用に近い", "like new"), ("新品", "new"), ("未使用", "unused"), ("未開封", "sealed"),
    ("未組立", "unassembled"), ("極美品", "mint condition"), ("美品", "excellent condition"),
    ("良品", "good condition"),
    ("目立った傷や汚れなし", "no noticeable scratches or stains"), ("目立った傷なし", "no noticeable scratches"),
    ("やや傷や汚れあり", "some scratches or stains"), ("やや傷あり", "some scratches"),
    ("傷や汚れあり", "scratches and stains"), ("傷あり", "scratches"),
    ("全体的に状態が悪い", "poor overall condition"),
    ("ジャンク品", "junk (for parts)"), ("ジャンク", "junk (for parts)"), ("部品取り", "for parts"),
    ("動作確認済み", "tested working"), ("動作確認済", "tested working"),
    ("元箱付き", "with original box"), ("元箱付", "with original box"), ("箱付き", "with box"),
    ("箱あり", "with box"), ("箱なし", "no box"), ("本体なし", "no camera body"),
    ("黒タグ付き", "with black tag"), ("タグ付き", "with tags"),
    ("帯付き", "with obi"), ("帯付", "with obi"), ("帯あり", "with obi"), ("帯なし", "no obi"),
    ("開封済み", "opened"), ("開封品", "opened"), ("欠品あり", "missing parts"),
    ("単品", "single item"), ("純正", "genuine"),
    ("シャッター回数", "shutter count"), ("シャッター数", "shutter count"),
    ("EVF不良", "EVF defective"), ("液晶漏れ", "LCD bleed"), ("モルト交換済み", "light seals replaced"),
    ("ボディ", "body"), ("レンズに薄クモリ", "slight lens haze"), ("薄クモリ", "slight haze"),
    ("クモリ", "haze"), ("カビ", "fungus"), ("履きジワあり", "creasing from wear"),
    ("ソール剥がれ", "sole separation"), ("盤面スレあり", "surface scuffs"),
    ("再発盤", "reissue"), ("再発", "reissue"), ("オリジナル盤", "original pressing"),
    ("年復刻", " reissue"), ("復刻", "reissue"), ("収録", "included"),
    ("USA製", "made in USA"), ("日本製", "made in Japan"),
    ("A賞", "Prize A"), ("ハイ", "High"), ("ロー", "Low"),
]

# Mercari's 6 listing grades (Yahoo Auctions uses the same wording) plus common title shorthand.
# Longest key wins; matched against NFKC text with whitespace and "、" removed.
CONDITION_TABLE: list[tuple[str, str]] = [
    ("全体的に状態が悪い", "junk"), ("ジャンク", "junk"), ("部品取り", "junk"), ("故障品", "junk"), ("動作不良", "junk"),
    ("未使用に近い", "like_new"), ("極美品", "like_new"), ("超美品", "like_new"), ("美品", "like_new"),
    ("新品未使用", "new"), ("新品未開封", "new"), ("未開封", "new"), ("未組立", "new"), ("未使用", "new"), ("新品", "new"),
    ("やや傷や汚れあり", "fair"), ("傷や汚れあり", "fair"), ("やや傷あり", "fair"), ("傷あり", "fair"),
    ("難あり", "fair"), ("訳あり", "fair"), ("汚れあり", "fair"), ("スレあり", "fair"), ("履きジワあり", "fair"),
    ("目立った傷や汚れなし", "good"), ("目立った傷なし", "good"), ("良品", "good"),
]

# Accessory nouns: a title with one of these plus a "for / only" marker is not the item itself.
ACCESSORY_NOUNS = ["バッテリー", "電池", "ケース", "フィルター", "フード", "ストラップ", "充電器", "キャップ",
                   "靴紐", "シューレース", "インソール", "説明書", "台座"]
ACCESSORY_MARKERS = ["専用", "用", "対応", "単品", "のみ"]
ACCESSORY_ALWAYS = ["空箱", "箱のみ", "本体なし", "説明書のみ"]

# Flaw words scanned in title + description (longest first; negation handled in normalize.py).
FLAWS: list[tuple[str, str]] = [
    ("ソール剥がれ", "sole separation"), ("液晶漏れ", "LCD bleed"), ("履きジワ", "creasing"),
    ("EVF不良", "EVF defect"), ("擦り傷", "scuffs"), ("日焼け", "sun fading"), ("クモリ", "haze"),
    ("曇り", "haze"), ("カビ", "fungus"), ("スレ", "scuffs"), ("擦れ", "scuffs"), ("欠品", "missing parts"),
    ("不良", "defect"), ("ヤケ", "sun fading"), ("タバコ", "smoke odor"), ("凹み", "dent"),
    ("ヘコミ", "dent"), ("反り", "warp"), ("汚れ", "stains"), ("キズ", "scratches"), ("傷", "scratches"),
]
SERIOUS_FLAWS = {"fungus", "haze", "EVF defect", "LCD bleed", "sole separation", "missing parts", "defect", "warp"}

CITY_POP_ARTISTS = ["山下達郎", "竹内まりや", "大貫妙子", "松原みき", "杏里", "角松敏生", "大滝詠一"]

# Rough packed weights in kg, used only when a listing does not state one.
DEFAULT_WEIGHT_KG = {"camera": 0.8, "battery_loose": 0.1, "camera_accessory": 0.3,
                     "sneakers": 1.2, "vinyl": 0.5, "figure": 0.6}

STOPWORDS = {
    "used", "secondhand", "second", "hand", "pre", "owned", "preowned", "a", "an", "the", "of", "for", "in", "on",
    "with", "and", "or", "to", "from", "me", "my", "i", "want", "need", "find", "buy", "looking", "look", "get",
    "under", "below", "less", "than", "max", "budget", "usd", "dollars", "dollar", "shipped", "shipping", "ship",
    "japan", "japanese", "jp", "please", "good", "nice", "condition", "size", "cm", "some", "any", "cheap", "best",
    "item", "items", "by", "at", "is", "that", "like", "new", "mint",
}
