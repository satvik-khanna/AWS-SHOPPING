# KAI

A Japan-focused personal shopping agent. It finds product announcements from Japanese creators on X, traces each one to the store that actually sells it, compares it with marketplace listings, applies your remembered preferences, estimates a cited delivered cost, and prepares a nonbinding purchase draft for you to review.

KAI never places an order, bid or payment. There is no code path that can.

For what has and has not been verified, read [IMPLEMENTATION_REPORT.md](IMPLEMENTATION_REPORT.md).

## Run it

```bash
uv sync
uv run python -m kai.main        # http://127.0.0.1:8000
```

With no credentials the app is fully usable on bundled sample data, which is labeled as sample data everywhere it appears. Set `KAI_ALLOW_FIXTURES=false` to see only real data.

```bash
uv run pytest                               # 112 tests, no network
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
- **Social Drops**: the announcement feed. **Refresh** searches on demand; nothing polls in the background. **Watched Creators** adds handles and search terms to the next refresh. **Import a post** takes a post URL plus its text.
- **Listings**: paste a product page URL to import it into future comparisons.
- **Review**: every draft waits here. Approving records your decision and nothing else.
- **Memory**: preferences used in ranking.
- **Activity**: integration states, usage and estimated X API spend, and the full log.

Categories understood: artisan keycaps, figures, cameras, handmade goods, stationery, fashion. A request containing any other Japanese product word (for example 香炉) searches for that word.

## Layout

```
kai/
  app.py              wiring and HTTP API
  orchestrator.py     request -> memory -> discovery -> marketplace -> cost -> ranking -> draft -> stop
  xdiscovery/         providers, Japanese query engine, extraction, storefront tracing, dedupe
  integrations/       Superlinked SIE, Senso, Cognee adapters
  net.py              bounded fetcher (public addresses only, robots.txt, budgets)
  security.py         untrusted-content handling
  cost.py             landed cost;  data/cost_sources.json holds the sources and parameters
  purchase.py         drafts and the review gate (no network access)
  fixtures/           sample posts, pages and listings, served from *.kai-fixture.invalid
  web/                the UI (no build step)
tests/                112 tests
scripts/live_check.py
```
