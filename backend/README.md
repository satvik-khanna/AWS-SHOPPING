# KAI

A personal agent for collectors buying from Japanese creators. It remembers your taste, finds product announcements on X, traces each one to the store that actually sells it, compares marketplace listings, estimates a cited delivered cost, and prepares a nonbinding purchase draft for you to review. Save a hunt to keep the brief and compare later checks for new matches, restocks, and price changes.

KAI never places an order, bid or payment. There is no code path that can.

For what has and has not been verified, read [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md).

## Run it

```bash
uv sync
uv run python -m kai.main        # http://127.0.0.1:8000
```

With no credentials the app is fully usable on bundled sample data, which is labeled as sample data everywhere it appears. Set `KAI_ALLOW_FIXTURES=false` to see only real data.

```bash
uv run pytest                               # 123 tests, no network
uv run python scripts/live_check.py         # what is configured, and what actually answers
uv run python scripts/live_check.py --listing https://minne.com/items/31172427
uv run python scripts/live_check.py --x-search keycaps    # one real X search; needs a token and costs money
```

## Credentials

Copy `.env.example` to `.env`. Everything is optional; each missing key produces a named fallback, shown in the header of the UI and on the Activity tab.

| Variable | Enables | Without it |
| --- | --- | --- |
| `X_BEARER_TOKEN` | Official X API v2 recent search, and importing a post by URL alone | No X request is made |
| `X_API_MAX_SPEND_USD` | Ceiling on estimated X API spend (default `1.00`) | |
| `BRAVE_SEARCH_API_KEY` | Search over publicly indexed X posts | Skipped |
| `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` | Translation, and extraction of fields the rules miss | Keyword glosses only; no translation is shown |
| `SIE_API_KEY` or `SIE_BASE_URL` | Superlinked SIE embeddings and reranking | Lexical n-gram ranking, labeled "not semantic" |
| `SENSO_API_KEY` | Cost citations retrieved through Senso | Citations from bundled copies of the same sources |
| `LLM_API_KEY` plus `uv sync --extra cognee` | Cognee graph memory | Local SQLite preference store |
| `RAKUTEN_APP_ID`, `RAKUTEN_ACCESS_KEY` | Live Rakuten Ichiba search | Imported listings and sample listings only |

After setting `SENSO_API_KEY`, press **Upload cost sources to Senso** on the Activity tab once. It writes seven documents to your Senso knowledge base, so it only runs when you ask.

## Using it

- **Agent**: type a request such as *Find me Japanese artisan keycaps announced recently on X, under $120 delivered to California.* You get the ranked comparison, the draft, and the timeline of every step.
- **Saved Hunts**: choose **Save hunt** on a comparison. **Check now** reruns the brief with current memory and compares returned products with previous observations. It refreshes relevant imported listings within the shared fetch budget, retains the baseline when a source fails, and does not create another draft. Checks run on demand; X posts and store pages can be cached for 15 minutes. Missing results do not mean a product sold out. Sample changes are labeled as sample data.
- **Social Drops**: the announcement feed. **Refresh** searches on demand; nothing polls in the background. **Watched Creators** adds handles and search terms to the next refresh. **Import a post** takes a post URL plus its text.
- **Listings**: paste a product page URL to import it into future comparisons.
- **Review**: every draft waits here. Approving records your decision and nothing else.
- **Memory**: preferences used in ranking.
- **Activity**: integration states, usage and estimated X API spend, and the full log.

Categories understood: artisan keycaps, figures, cameras, handmade goods, stationery, fashion. A request containing any other Japanese product word (for example 香炉) searches for that word.

## Hackathon demo

Suggested pitch: **KAI remembers a collector's taste and keeps track of Japanese creator drops, so the collector can review what fits, what changed, and what it may cost to get home.**

The [tokens& Personal Agents Hackathon organizer page](https://luma.com/personalagenthack) asks for personal agents beyond chat interfaces and lists project submission at 4:30 PM on November 20, 2026, in San Francisco. The public page does not specify a scoring rubric or mandatory vendor stack; confirm any additional instructions given at the venue.

Keep the live demo to one category and one destination:

1. In **Memory**, save “I prefer handmade pieces with a matte finish, not glossy.”
2. In **Agent**, run the keycaps request above. Show the preferences applied, the Japanese post, and the traced product page. Point out which data is live, imported, cached, or sample.
3. Open the delivered-cost breakdown. Explain that shipping, tax, duty, and proxy costs include labeled assumptions; this is an estimate, not a checkout quote.
4. Select **Save hunt**, then **Check now** in **Saved Hunts**. Show the previous observations and the current comparison. Unchanged data should produce no new changes and no duplicate review draft.
5. Open **Review** and show the evidence chain and the purchase handoff.

For reproducible restock and price-drop evidence, run `uv run pytest tests/test_hunts.py -q`. Those tests change a **simulated** store from sold out at ¥9,000 to in stock at ¥6,000; they also test source failure, recovery, persistence, current memory, and duplicate suppression. This is verification of behavior, not a live restock claim.

Before presenting a live agent, configure and verify a discovery provider and an LLM using `scripts/live_check.py --x-search keycaps`. Check any sponsor integration you plan to name. As of the latest local verification, none of those credentials are configured; the workflow is tested on sample data and simulated services. The updated Saved Hunts UI has not been visually verified because browser tooling was unavailable. Periodic background monitoring and outbound notifications are not implemented.

## Layout

```
kai/
  app.py              wiring and HTTP API
  orchestrator.py     request -> memory -> discovery -> marketplace -> cost -> ranking -> draft -> stop
  hunts.py            persisted briefs, observed changes, and explicit rechecks without duplicate drafts
  xdiscovery/         providers, Japanese query engine, extraction, storefront tracing, dedupe
  integrations/       Superlinked SIE, Senso, Cognee adapters
  net.py              bounded fetcher (public addresses only, robots.txt, budgets)
  security.py         untrusted-content handling
  cost.py             landed cost;  data/cost_sources.json holds the sources and parameters
  purchase.py         drafts and the review gate (no network access)
  fixtures/           sample posts, pages and listings, served from *.kai-fixture.invalid
  web/                the UI (no build step)
tests/                123 tests
scripts/live_check.py
```
