# KAI implementation report

Written 2026-11-20, at the end of the build session.

## Follow-up verification on November 20

The current build adds persistent Saved Hunts: save an existing comparison as a baseline, recheck the same brief on demand, and report new qualifying matches, restocks, listed-price changes, meaningful delivered-cost changes, and products that now fit the brief. Each change retains before/after evidence and sample provenance. Rechecks refresh matching imported listings within the run's fetch budget, preserve observations across failed or partial searches, and do not create duplicate drafts. Product identity retains variant query parameters. Checks are serialized per hunt within the local process; deletion during a check is refused. There is no scheduler or outbound notification service. Posts and pages can be cached for 15 minutes.

Personalization now separates positive and negative request attributes. “Matte and not glossy” no longer promotes glossy products. A current brief can override stored taste for that search; an explicit lasting preference is remembered. Rechecking an old brief does not relearn its old preference statement into current memory. Automatic draft selection avoids known conflicting attributes. Zero-dollar budgets no longer divide by zero.

Verification: **123 tests pass**, with outbound network calls mocked, plus Python compilation and JavaScript syntax checks. The new tests exercise real API/service paths against a simulated changing storefront, failure and recovery, persistent baselines, updated preferences, concurrent checks, and unchanged rechecks. Browser verification of the updated UI was attempted but blocked by an unavailable browser connection and a native Chrome tool timeout. The local server starts successfully. No X, LLM, SIE, Senso, Cognee, or Rakuten credentials are configured, so their live behavior remains unverified. The original build-session results below are historical.

## Summary

The end-to-end workflow runs: search, memory, ranking, cited cost, purchase draft, human review. X discovery is implemented as a P0 feature with four providers. No X, Superlinked SIE, Senso, Cognee or LLM call was made against a live service in this session, because no credentials for any of them exist on this machine. Those adapters are tested against simulated servers only.

Two things did run live: the exchange-rate lookup, and the import of two real store listings.

The "complete master specification" the request refers to was not in the message. This build follows the requirements that were in the message; anything that only the spec defined is not covered.

## What ran, and how

| Area | Status | Evidence |
| --- | --- | --- |
| Test suite | 112 passed | `uv run pytest`, all network mocked |
| App in a browser | Worked | Ran the required request, refreshed Social Drops, opened Review; every tab loaded without script errors |
| FX rate (Frankfurter) | Live call succeeded | 1 JPY = 0.0063034 USD, dated 2026-11-20 |
| Listing import | Live call succeeded on minne | `minne.com/items/31172427` read as ¥1,600 in stock; `minne.com/items/37942152` read as ¥1,500 sold out. Both matched the pages. |
| X official API | Not attempted: no `X_BEARER_TOKEN` | Simulated responses only |
| Indexed search (Brave) | Not attempted: no key | Simulated responses only |
| Superlinked SIE | Not attempted: no key or endpoint | Simulated responses only |
| Senso | Not attempted: no key | Simulated responses only |
| Cognee | Not attempted: no `LLM_API_KEY` | Fake module in tests. Separately installed cognee 1.6.10 in a throwaway environment and confirmed `remember(data, dataset_name=...)` and `recall(query_text, datasets=..., top_k=...)` match what the adapter calls. No Cognee call was made. |
| LLM translation and extraction | Not attempted: no key | Simulated responses only |
| Rakuten Ichiba search | Not attempted: no key | Not tested beyond being unconfigured |

## The required test case

Request: *Find me Japanese artisan keycaps announced recently on X, under $120 delivered to California.*

Run with no credentials, which is the only way it could run here:

| Step | Result |
| --- | --- |
| A. Japanese queries | Six generated, all valid by the local validator, including `"キーキャップ" (新作 OR 限定 OR 再販) lang:ja -is:retweet` |
| B. Real authorized X discovery | Not possible. Both live providers report "not configured" and no request is sent. |
| C. Three relevant leads | Seven leads, all from bundled sample posts and labeled as such. The run states it is not fresh X discovery. |
| D. Translate and classify | Classified by rules (new release, lottery, preorder, sold out, restock). Translations are the ones bundled with the sample posts. Real posts would get keyword glosses only until an LLM key is set. |
| E. Resolve a storefront | Five sample store pages resolved, including link page to store to product. Announcement URL and purchase URL are kept separate. |
| F. Compare with other results | Six sample marketplace listings ranked alongside |
| G. Preferences and cost | "Handmade, matte, not glossy" applied from the local store. Top match estimated at $105.24 delivered with seven cited sources. |
| H. Draft | Nonbinding proxy request draft prepared for the top in-budget match |
| I. Stop before binding action | Draft is `PENDING_REVIEW`; the run ends with a "stopped" event |
| J. Timeline | Every step from request to stop is logged and shown |

The same case is also tested against a simulated X API (`tests/test_e2e.py`). There it reports fresh discovery, uses no sample posts, resolves a store from the API's expanded URL, and contacts only `api.x.com` and the two linked stores. That shows the live code path is wired correctly; it does not show that X accepts the requests.

## What was built

- **X discovery.** `XOfficialAPIProvider`, `IndexedWebSearchProvider`, `ManualPostImportProvider`, `FixtureProvider`. Official first, indexed search if that returns fewer than three posts, imported posts always, sample posts only when the total is still under three. Each provider reports state, retrieval time, coverage and error.
- **Query engine.** Three to six queries per category, split by intent, built from category terms, stored preferences, watched creators and terms. No popularity operators; results are requested by recency.
- **Lead extraction.** The `SocialProductLead` structure as specified, with the eight announcement types. Rules always run. LLM output is accepted only where the value appears in the post text. Missing fields stay empty.
- **Storefront tracing.** Recognizes BOOTH, BASE, STORES, Shopify, minne, Creema, SUZURI, Mercari, Yahoo, Rakuten, Amazon JP, independent stores, and link pages (Linktree, Beacons, lit.link and others). Follows at most two hops. Reads price, availability, restrictions and overseas shipping. Lottery, preorder, made-to-order and sold-out are distinct from in-stock. Pages it cannot read become manual-only leads with no purchase link.
- **Dedupe.** Same post, same product page, same creator and name, or near-identical text.
- **Memory, ranking, cost.** Preferences with like and avoid; five-part score with the backend named on every result; eight cost lines, each marked observed, sourced or assumption.
- **Purchase gate.** Three outcomes: proxy request draft, manual-only notes, watch-only. Drafts have three states and none of them is "submitted".
- **UI.** Agent, Social Drops with Watched Creators and post import, Shortlist, Review, Listings, Memory, Activity.

## Safety properties, and what checks them

- **No binding action.** `kai/purchase.py` imports no network library, and no API route name contains order, pay, bid, checkout or submit. Tests assert both. Approving a draft changes a status field.
- **Untrusted pages.** Post and page text is never used as instructions. The purchase path and the choice of draft are decided by plain code reading parsed fields, not by model output. Instruction-like text, including hidden elements and HTML comments in English and Japanese, is flagged, lowers the trust score, and appears as a warning on the draft. A sample page that tells agents to buy it immediately is in the fixtures; a test asserts it does not win.
- **Access limits.** 401, 403 and 429 from X, the search API or a store stop the provider or mark the lead blocked. Tests assert exactly one request is made. `robots.txt` is honored. There is no login automation and x.com itself is never fetched.
- **Spend ceiling.** Before each X request the worst-case cost is checked against `X_API_MAX_SPEND_USD`. Results are cached for 15 minutes.
- **Fetch bounds.** http and https only, public addresses only, checked again on every redirect; 8 second timeout, 1.5 MB cap, five redirects, twelve store requests per run, three at a time.
- **No invented links.** Sample posts and stores live on `*.kai-fixture.invalid`, a reserved domain, and are served from disk. The UI shows them as text, not links.

## Limitations you should know about

- **Live X behavior is unverified.** Query syntax is checked by a local validator written from X's documented rules. X may reject operators depending on the access tier. The per-post and per-user prices used for the spend estimate ($0.005 and $0.010) are configurable assumptions; check them against your plan.
- **Store parsers are verified live on minne only.** BOOTH, BASE, STORES and Shopify parsing is tested against sample pages written for this project, which may not match the real markup. Stores that render with JavaScript will mostly come back as unsupported.
- **Senso citations need seeding.** Until the sources are uploaded, a configured Senso returns nothing that maps to them and KAI cites the bundled copies, saying so.
- **Cost estimates are rough.** Duty is a flat 12.5% of item value with no tariff classification. Package weight, domestic shipping, proxy fee and the California use tax rate (10.25%) are assumptions, labeled as such. The seven sources were found by web search or fetched on 2026-11-20, and the stored text is my summary with short excerpts, not the full documents. Two are third-party pages (a customs software vendor for the tariff rate, an export merchant for the clearance fee). Re-check every number before relying on it. Only U.S. destinations are estimated.
- **The proxy draft is text.** No proxy service is integrated. KAI has not checked that any service accepts a given store.
- **The address check has a gap.** The fetcher resolves a hostname, checks the address, then lets the HTTP client resolve it again. A hostile DNS server could answer differently the second time. Pinning the resolved address would close this.
- **Local, single user.** No authentication; the server binds to 127.0.0.1. Do not expose it.
- **Refresh is on demand only.** Periodic refresh was optional and is not built.
- **Fallback ranking is lexical.** Without SIE it matches character n-grams, so it does not understand synonyms.
- **Product names** are taken only from 「」 or 【】 markers unless an LLM is configured.

## Housekeeping

- Nothing was committed. The enclosing git repository is your home directory, so no `git init` or commit was made here.
- Verifying the Cognee signatures created `~/.cognee/logs/` with two small log files. The throwaway environment itself was removed.
- `var/` holds the app's SQLite database and is ignored by git.
