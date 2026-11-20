# NOTES-listings

1. Works: 68 raw Japanese fixtures: Mercari / Yahoo Auctions (cameras, sneakers, vinyl, figures, Jp-vintage sweatshirts), 13 Off Mall (Hard Off / Hobby Off / Mode Off / Liquor Off / Garage Off, rank N-J) and 7 BOOTH / BASE indie goods. EN -> JA keyword expansion incl. 'A or B' queries and 'on BOOTH/BASE' channels, condition normalization (目立った傷や汚れなし -> good, Hard Off rank J -> junk), field extraction (size cm / letter / Kapital 1-5, shutter count, box, obi, grades, junk_risk repairable vs parts only, 100V, edition size, made to order, flaws).
2. Ranker `SmartRanker`: 0.45 relevance + 0.30 value + 0.15 condition + preference adjustments (shoe cm / apparel letter from query or memory, notes, past purchases, flaws, missing asked-for words, junk repairability, heavy items, duty share); one plain-English reason per pick with a 'Watch out:' part. The 4 survey demo queries are pinned in tests/test_rank.py DemoQueryTest.
3. Run: `python3 -m shopper "Fujifilm X100V" --budget 900` ; `python3 -m unittest discover -s tests -t .` (tests: tests/test_listings.py, tests/test_rank.py). Wired in shopper/app.py via `default_source()` / `default_ranker()`.
4. Live (`SHOPPER_LIVE=1`): real Yahoo! Auctions / Yahoo! Flea Market listings read from auctions.yahoo.co.jp/search/search?p= (robots.txt allows it; 1 req/s; plain UA) with real title, JPY price, condition code, seller, URL, image; every listing has fields data_origin=live, fetched_at, fetched_from. Buyee (robots.txt 403), Off Mall (robots Disallow /search/) and Mercari (client-rendered, API disallowed; in Chrome via dt-ax it shows a region gate and the AX tree has no link URLs) are not crawled. If nothing live is found the fixtures are used and labelled data_origin=fixture. Superlinked SIE rerank (backend/kai SIE client) runs with SIE_API_KEY / SIE_BASE_URL; Claude rerank with ANTHROPIC_API_KEY (paid, never run).
5. Fake: the fixture listings (labelled data_origin=fixture) are invented. With real data, 'Fujifilm X100V' --budget 900 finds 14 real X100V listings but none under $900 landed (cheapest real body about $1,296), so it answers 'no candidate within budget'; --budget 1500 returns real picks. SIE untested against a live server (no key here, httpx not installed for system python).

## Requests for shared files (not changed by me)

- `models.Listing.source` is `Literal["mercari", "yahoo_auctions", "other"]`; Yahoo! Flea Market cards from the Buyee Yahoo search come back as `other` with `fields["marketplace"]`. A `"yahoo_fleamarket"` value would be cleaner.
- `pipeline.run` could append `deps.source.last_status` and `deps.ranker.last_status` (when present) to the trail so the demo shows "live Buyee blocked -> fixtures" and "claude rerank" vs "offline ranking".

## Demo queries (offline, placeholder cost rules)

- `"limited-edition risograph artbook or acrylic stand from Japanese indie artist on BOOTH/BASE under $80, shipped to US-CA"` --budget 80 -> BOOTH / BASE goods, reason says the shop does not ship abroad and the proxy forwards.
- `"Kapital or vintage Champion Reverse Weave sweatshirt, size L, under $180, shipped to US-CA"` --budget 180 -> #1 90s Champion RW tagged L (m043); 80s RW m044 and the Kapital size 3 m041 are dropped as over budget once duty + EMS are added with the cost pane rules; wrong sizes sink with "tagged M, you wear L".
- `"junk Sony Walkman or Game Boy from Hard Off Net Mall under $60, shipped to US-CA"` --budget 60 -> power-on-checked junk first, "parts only" (液漏れ / 通電不可) last, every junk pick says no returns.
