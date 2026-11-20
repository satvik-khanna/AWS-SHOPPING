# Japan Shopping Agent — Niche Genres, Channels & Landed-Cost Rules Survey

- **As of**: `2026-11-20`
- **Scope**:
  1. High-demand Japan-to-US niche product genres & discovery channels (major marketplaces + Hard Off nationwide stores + Instagram/Linktree/Beacons/Shopify/BOOTH/BASE indie creators)
  2. Primary-source replacement values (`as_of` + URL) for every `PLACEHOLDER` in `fixtures/rules.json`
  3. Reference tables for `shopper/cost` (EMS weight bands, US post-*de minimis* duties, prohibited items) and `shopper/listings` (Japanese condition normalization & Hard Off junk vocabulary)

---

## 1. Key 2026 Macro Context (Why Cited Landed Cost Matters Now)

1. **U.S. $800 *de minimis* (Section 321) Exemption Suspended**: Low-value commercial parcels from Japan no longer enter the U.S. duty-free. Proxy services (including Buyee) have moved to **DDP (Delivered Duty Paid)**, making upfront, cited landed-cost estimation essential before checkout.
2. **Weak Yen Continues (USD/JPY ≈ 158.9)**: Trading range on `2026-11-20` is **158.69–159.14 JPY/USD** (vs. `150.0` placeholder), preserving strong Japan-to-US price arbitrage even after proxy fees, shipping, and duties.

---

## 2. Recommended Niche Genres & Discovery Channels (7 Categories)

| # | Genre | Example Items | Channels (Major + Indie/Niche) | Key Structured `fields` to Extract | Memory Hooks (`Preferences`) | Cost / Duty / Safety Edge Cases |
|---|---|---|---|---|---|---|
| **1** | **Used & Vintage Japanese Cameras / Lenses** | Fujifilm X100V / X100VI, Contax T2, Ricoh GR III, film SLRs | Mercari, Yahoo Auctions, specialty camera shops | `shutter_count`, `box_included`, `fungus_haze`, `battery_installed` | Condition standards (no fungus/haze), color preference | Digital cameras: 0% MFN + ~12.5% 2026 US baseline/DDP. **Installed lithium battery OK (up to 2/pkg); loose spare battery (`battery_loose`) prohibited.** |
| **2** | **Indie Creator Goods & Limited Merch** | Risograph art books, doujinshi, acrylic stands, enamel pins, handmade plushies, VTuber/artist drops | **BOOTH.pm**, **BASE**, **STORES.jp**, **Instagram bio → Linktree / Beacons → Shopify** | `edition_size`, `preorder_deadline`, `ships_abroad`, `creator_handle` | Favorite artists, past purchases (dedup) | Most Japanese indie shops do not ship abroad (`ships_abroad: false`). **Buyee free package consolidation** + 30-day free storage optimizes multi-shop shipping. |
| **3** | **JDM Streetwear & "Jp-Vintage" Fashion** | Kapital, Visvim, Neighborhood, Comme des Garçons, vintage 80s/90s Levi's / Champion Reverse Weave | Mercari, Yahoo Auctions, BASE boutiques, MODE OFF, Instagram drops | `size_jp`, `measurements_cm`, `material`, `flaws_ja` | **`Preferences.sizes`** (JP↔US conversion), fabric preferences | **Apparel (HTS Ch.61/62) has high baseline US duties (5–16%+)** — great demo for rejecting items that look cheap in JPY but exceed total landed budget. |
| **4** | **Japanese TCG, Collectibles & Retro Games** | Pokémon JP promo cards, One Piece TCG, Ichiban Kuji prizes, boxed Game Boy / PS1 / SFC | Mercari, Yahoo Auctions, HOBBY OFF, Magi, Suruga-ya | `psa_grade`, `sealed`, `box_manual_included`, `region_lock` | `rejected_sellers` (counterfeits / poor packing), strict mint grading | Lightweight (low EMS cost), high arbitrage; modern toys/cards fall under HTS Ch.95 (~12.5% DDP). |
| **5** | **JDM Watches & Artisanal Crafts / Stationery** | JDM Seiko / Grand Seiko, handmade ceramics (Mashiko/Shigaraki), fountain pens, Hobonichi accessories | Yahoo Auctions, **Creema**, **minne**, **BASE**, Instagram ceramicists | `case_diameter_mm`, `movement_type`, `ceramic_artist`, `nib_size` | Wrist size, favorite artisans | Watches (HTS Ch.91): movement + case component duties (~15% est.). Tableware/food-contact ceramics can trigger extra US courier documentation. |
| **6** | **City Pop LPs & Japanese Media** | 80s City Pop LPs with obi (`帯付き`), game OST vinyl/CDs, indie cassettes | Yahoo Auctions, Mercari, Discogs JP shops, Bandcamp/Linktree | `obi_included`, `vinyl_grade` (NM/EX/VG+), `pressing_year` | Minimum grade (VG+), owned titles | Books/CDs/LPs may qualify for informational-media duty exemptions vs. general merchandise. Contrast with strictly banned **perfumes (`perfume`)** and **weapons (`knife_weapon`)**. |
| **7** | **Nationwide Hard Off Group Stores (A–C Used + Vintage Junk)** | **HARD OFF** (audio, Walkman, cameras, synths, guitars, retro consoles, junk), **HOBBY OFF** (TCG/figures/games), **MODE OFF** (apparel), **OFF HOUSE** (outdoor/living/brands), **GARAGE OFF** (JDM car/bike parts), **LIQUOR OFF** (whisky/sake — *prohibited block test*) | **Hard Off Net Mall / Off Mall (`https://netmall.hardoff.co.jp/`, official Buyee partner)** — 1,100+ physical stores nationwide | `store_name`, `hardoff_brand`, `rank` (N/S/A/B/C/J), `junk_reason`, `voltage_jp` (`100V`), `missing_parts` | Junk tolerance (`condition_min: "junk"` vs `"good"`), `100V` gear tolerance | **Store-by-store domestic shipping** (Buyee 30-day free storage + free consolidation). **Junk (`J`) is strictly non-returnable/no-warranty even via Buyee.** Heavy amps (5–10kg) have steep EMS costs. **GARAGE OFF auto parts: ~15% Section 232 duty. LIQUOR OFF (`alcohol`): prohibited for international shipping.** |

---

## 3. Researched `fixtures/rules.json` Values (`as_of: 2026-11-20`)

```json
{
  "_note": "Researched Japan->US proxy landed-cost rules as of 2026-11-20. US $800 de minimis (Section 321) is suspended; Buyee collects US duties via DDP.",
  "fx_jpy_per_usd": {
    "value": 158.9,
    "as_of": "2026-11-20",
    "url": "https://tradingeconomics.com/japan/currency"
  },
  "proxy_service_fee_jpy": {
    "value": 500,
    "as_of": "2026-11-20",
    "url": "https://buyee.jp/helpcenter/guide/fees"
  },
  "proxy_standard_plan_jpy": {
    "value": 500,
    "as_of": "2026-11-20",
    "url": "https://buyee.jp/helpcenter/guide/fees"
  },
  "domestic_shipping_jpy": {
    "value": 750,
    "as_of": "2026-11-20",
    "url": "https://jp-news.mercari.com/"
  },
  "intl_shipping_jpy_per_kg": {
    "value": 5300,
    "as_of": "2026-11-20",
    "url": "https://www.post.japanpost.jp/send/oversea/charge/list-ems/all_en.html"
  },
  "default_weight_kg": {
    "value": 1.0,
    "as_of": "2026-11-20",
    "url": "https://www.post.japanpost.jp/send/oversea/charge/list-ems/all_en.html"
  },
  "duty_rate_by_category": {
    "value": {
      "camera": 0.125,
      "collectibles": 0.125,
      "indie_merch": 0.125,
      "electronics_junk": 0.125,
      "watch": 0.15,
      "auto_parts": 0.15,
      "apparel": 0.16,
      "antique_art": 0.0,
      "media_book_vinyl": 0.0,
      "default": 0.125
    },
    "as_of": "2026-11-20",
    "url": "https://buyee.jp/helpcenter/guide/fees"
  },
  "banned_categories": {
    "value": ["battery_loose", "power_bank", "perfume", "aerosol", "knife_weapon", "alcohol"],
    "as_of": "2026-11-20",
    "url": "https://buyee.jp/helpcenter/guide/prohibited-items"
  }
}
```

### Japan Post EMS Zone 4 (United States) Official Weight-Band Table
- **Source**: `https://www.post.japanpost.jp/send/oversea/charge/list-ems/all_en.html` (verified HTTP 200 on `2026-11-20`; note Feb 1, 2027 revision raises `<=1kg` to `¥7,400`)

| Max Weight | EMS Rate to U.S. (JPY) |
|---|---|
| ≤ 0.5 kg | 3,900 |
| ≤ 0.6 kg | 4,180 |
| ≤ 0.7 kg | 4,460 |
| ≤ 0.8 kg | 4,740 |
| ≤ 0.9 kg | 5,020 |
| ≤ 1.0 kg | 5,300 |
| ≤ 1.25 kg | 5,990 |
| ≤ 1.5 kg | 6,600 |
| ≤ 1.75 kg | 7,290 |
| ≤ 2.0 kg | 7,900 |
| ≤ 2.5 kg | 9,100 |
| ≤ 3.0 kg | 10,300 |

---

## 4. Japanese Condition Normalization & Hard Off Junk Vocabulary

### Mercari / Yahoo Auctions 6-Tier Condition Mapping
- `新品、未使用` / `未使用` / `新品` → `"new"`
- `未使用に近い` / `極美品` / `美品` → `"like_new"`
- `目立った傷や汚れなし` / `良品` → `"good"`
- `やや傷や汚れあり` / `傷や汚れあり` / `傷あり` → `"fair"`
- `全体的に状態が悪い` / `ジャンク` / `ジャンク品` → `"junk"`

### Hard Off Net Mall (`netmall.hardoff.co.jp`) Junk Vocabulary
- **Potentially repairable / untested**: `通電確認済み` / `通電確認のみ` (power-on confirmed), `動作未確認` (untested), `ベルト劣化` (belt degraded), `ACアダプター欠品` (missing AC adapter)
- **High-risk / parts-only**: `通電不可` (won't power on), `液漏れ` (battery corrosion), `レンズ内カビあり` (lens fungus), `部品取り` (for parts), `現状渡し` (as-is, no return)
- **Buyer warnings**: No returns/warranty on `J` rank even via Buyee; Japan domestic `100V` vs U.S. `120V`; per-store domestic shipping.

---

## 5. Sources (Checked `2026-11-20`)

- Buyee Fee Schedule (¥500 flat service fee, optional plans, free consolidation): https://buyee.jp/helpcenter/guide/fees
- Buyee International Shipping Fees: https://buyee.jp/helpcenter/guide/shipping-fees
- Buyee Prohibited Items (loose lithium batteries, perfumes, aerosols, weapons, alcohol): https://buyee.jp/helpcenter/guide/prohibited-items
- Japan Post EMS Postage Rates (Zone 4 — United States): https://www.post.japanpost.jp/send/oversea/charge/list-ems/all_en.html
- USD/JPY Exchange Rate (Nov 20, 2026: 158.69–159.14): https://tradingeconomics.com/japan/currency
- Hard Off Net Mall / Off Mall (1,100+ Group stores, 6 formats, Buyee partner): https://netmall.hardoff.co.jp/
- Mercari Japan Rakuraku Mercari-bin Takkyubin 60-size (¥750): https://jp-news.mercari.com/
