# NOTES-listings

1. Works: 68 raw Japanese fixtures: Mercari / Yahoo Auctions (cameras, sneakers, vinyl, figures, Jp-vintage sweatshirts), 13 Off Mall (Hard Off / Hobby Off / Mode Off / Liquor Off / Garage Off, rank N-J) and 7 BOOTH / BASE indie goods. EN -> JA keyword expansion incl. 'A or B' queries and 'on BOOTH/BASE' channels, condition normalization (目立った傷や汚れなし -> good, Hard Off rank J -> junk), field extraction (size cm / letter / Kapital 1-5, shutter count, box, obi, grades, junk_risk repairable vs parts only, 100V, edition size, made to order, flaws).
2. Ranker `SmartRanker`: 0.45 relevance + 0.30 value + 0.15 condition + preference adjustments (shoe cm / apparel letter from query or memory, notes, past purchases, flaws, missing asked-for words, junk repairability, heavy items, duty share); one plain-English reason per pick with a 'Watch out:' part. The 4 survey demo queries are pinned in tests/test_rank.py DemoQueryTest.
3. Run: `python3 -m shopper "Fujifilm X100V" --budget 900` ; `python3 -m unittest discover -s tests -t .` (tests: tests/test_listings.py, tests/test_rank.py). Wired in shopper/app.py via `default_source()` / `default_ranker()`.
4. Live (opt-in): `SHOPPER_LIVE=1` -> Buyee public search pages for Mercari + Yahoo Auctions; Buyee answers plain HTTP with an AWS WAF challenge (seen 2026-11-20), so in practice it falls back to fixtures and says so on stderr. `SHOPPER_LIVE=1` + `ANTHROPIC_API_KEY` -> Claude rerank of the top 8 (paid; never run in this session), any error -> offline order.
5. Fake: every fixture listing (ids, sellers, stores, prices, URLs incl. netmall.hardoff.co.jp/product/<id>/ format) is invented; Buyee HTML parsers never ran against a live page; Claude rerank only tested with a stub; no Superlinked adapter. Fixture categories apparel / indie_merch / electronics_junk / alcohol / auto_parts / collectibles follow the survey's duty table, but camera / sneakers / vinyl / figure keep their old names (cost pane: map vinyl -> media_book_vinyl if you use the survey table).

## Requests for shared files (not changed by me)

- `models.Listing.source` is `Literal["mercari", "yahoo_auctions", "other"]`; Yahoo! Flea Market cards from the Buyee Yahoo search come back as `other` with `fields["marketplace"]`. A `"yahoo_fleamarket"` value would be cleaner.
- `pipeline.run` could append `deps.source.last_status` and `deps.ranker.last_status` (when present) to the trail so the demo shows "live Buyee blocked -> fixtures" and "claude rerank" vs "offline ranking".

## Demo queries (offline, placeholder cost rules)

- `"limited-edition risograph artbook or acrylic stand from Japanese indie artist on BOOTH/BASE under $80, shipped to US-CA"` --budget 80 -> BOOTH / BASE goods, reason says the shop does not ship abroad and the proxy forwards.
- `"Kapital or vintage Champion Reverse Weave sweatshirt, size L, under $180, shipped to US-CA"` --budget 180 -> #1 90s Champion RW tagged L (m043); 80s RW m044 and the Kapital size 3 m041 are dropped as over budget once duty + EMS are added with the cost pane rules; wrong sizes sink with "tagged M, you wear L".
- `"junk Sony Walkman or Game Boy from Hard Off Net Mall under $60, shipped to US-CA"` --budget 60 -> power-on-checked junk first, "parts only" (液漏れ / 通電不可) last, every junk pick says no returns.
