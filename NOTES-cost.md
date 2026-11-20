# NOTES-cost

1. Works: `shopper/cost` computes landed cost Japan -> US via Buyee from researched `fixtures/rules.json` (no PLACEHOLDER left; every rule has value, as_of, url, status verified|estimate): FX (Fed H.10), Buyee fee + plan + payment + customs-clearance fee, Mercari domestic size bands, Japan Post EMS / airmail / seamail weight bands to the US with method choice (`cheapest_tracked` default, `cheapest`, `fastest`), US duty by category (HTS MFN) + Japan 12.5% floor (HTS 9903.05.48/49, informational materials exempt) with de minimis suspended, prohibited items and lithium count -> `blocked_reason`.
2. Every CostLine has a Citation (`fixtures/rules.json#<rule>[.<sub>]`, as_of, url; estimates are tagged `(estimate)` in the source).
3. Run: `python3 -m shopper "Fujifilm X100V" --budget 900` and `python3 -m unittest discover -s tests -t .` (cost tests: `tests/test_cost.py`).
4. Fake / estimate: default weight 1.0 kg, packaging 0.2 kg, watch compound duty approximated, 5% default duty for unknown categories, non-US duty not modeled ($0 line), Buyee Air Delivery / ECMS rates not modeled (no public table).
5. Senso: `shopper/cost/senso.py` reads the same rules JSON from Senso only when `SHOPPER_LIVE=1` + `SENSO_API_KEY` + `SENSO_RULES_CONTENT_ID`; Senso API path (`/content/{id}`, `X-API-Key`) is a guess, not verified; any failure falls back to the local file.

## Details

- Sources checked 2026-11-20: buyee.jp/helpcenter/guide/{fees,consolidate,prohibited,lithium-battery,shipping-method}, post.japanpost.jp EMS all_en + list-normal/zone4_en, cbp.gov e-commerce FAQ (de minimis indefinitely suspended, EO 14324 as amended by EO 14388), supremecourt.gov slip opinion 24-1287 (IEEPA does not authorize tariffs), cbp.gov IEEPA refunds, hts.usitc.gov API, help.jp.mercari.com articles 134/136, federalreserve.gov H.10.
- Japan duty: IEEPA tariffs struck 2026-02-20; Section 122 10% (9903.03.01) ran 2026-02-24..07-24 (CBP CSMS 67844987) and is NOT applied; current HTS 9903.05.48/49 (US note 52) = max(MFN, 12.5%) for Japanese goods, exempt informational materials (9903.05.92). Secondary sites call note 52 the Section 301 replacement; we cite the HTS text.
- SURVEY.md corrections: the 12.5% camera number is right in effect but its basis is HTS 9903.05.49, not a 'DDP baseline' (MFN 8525.89.40 is Free); the prohibited-items URL is /prohibited, not /prohibited-items; Buyee lists knives only as a FedEx restriction, not all-carrier.
- Requests for shared files (not changed by me): none so far.
