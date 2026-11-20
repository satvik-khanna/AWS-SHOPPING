# NOTES-listings

1. Works: 40 raw Japanese Mercari / Yahoo Auctions fixtures (12 camera, 9 sneakers, 8 vinyl, 8 figure + accessories / junk / a loose battery), English -> Japanese keyword expansion, condition normalization (目立った傷や汚れなし -> good, ジャンク in title always wins), field extraction (size cm, shutter count, box, obi, sealed, grade, edition, tested, flaws, accessory-only).
2. Ranker `SmartRanker`: 0.40 relevance + 0.30 value (budget headroom + cheapness vs peers) + 0.20 condition + preference adjustments (shoe size, notes like "box"/"original"/"obi", past purchases, flaws, shutter count); drops rejected sellers and below-floor condition; one plain-English reason per pick with a "Watch out:" part.
3. Run: `python3 -m shopper "Fujifilm X100V" --budget 900` ; `python3 -m unittest discover -s tests -t .` (tests: tests/test_listings.py, tests/test_rank.py). Wired in shopper/app.py via `default_source()` / `default_ranker()`.
4. Live (opt-in): `SHOPPER_LIVE=1` -> Buyee public search pages for Mercari + Yahoo Auctions; Buyee answers plain HTTP with an AWS WAF challenge (seen 2026-11-20), so in practice it falls back to fixtures and says so on stderr. `SHOPPER_LIVE=1` + `ANTHROPIC_API_KEY` -> Claude rerank of the top 8 (paid; never run in this session), any error -> offline order.
5. Fake: all fixture listings (ids, sellers, prices, URLs are invented), the Buyee HTML parsers are written against archived markup and never ran against a live page, the Claude rerank is only tested with a stubbed response; no Superlinked adapter yet.

## Requests for shared files (not changed by me)

- `models.Listing.source` is `Literal["mercari", "yahoo_auctions", "other"]`; Yahoo! Flea Market cards from the Buyee Yahoo search come back as `other` with `fields["marketplace"]`. A `"yahoo_fleamarket"` value would be cleaner.
- `pipeline.run` could append `deps.source.last_status` and `deps.ranker.last_status` (when present) to the trail so the demo shows "live Buyee blocked -> fixtures" and "claude rerank" vs "offline ranking".
