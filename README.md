# AWS-SHOPPING
Personal shopping agent that finds niche products and orders them only after you approve.
Japan Shopping Agent

Problem: Japanese marketplaces like Mercari and Yahoo Auctions carry items you can’t get elsewhere. But the listings are in Japanese, many sellers won’t ship abroad, and the real cost is hidden across proxy fees, shipping and duties.

- Instagram profiles and links in bios
- Linktree and Beacons pages
- Small independent online stores
- Niche fan communities
- Independent artists and creators
- Limited-edition merchandise and handmade collectibles
- Products sold through small communities rather than major marketplaces

What it does: You describe what you want (“used Fujifilm X100V under $900, shipped to California”). The agent then:

Recalls your preferences and past purchases.
Searches Japanese marketplaces, translating and extracting details from each listing.
Ranks the best matches and works out the full landed cost of each, citing the fee and duty rules behind every number.
Fills the cart on a proxy service (Buyee) and stops for your approval before anything is paid.

Why it fits the challenge: it uses trusted context to decide, takes a real action (the checkout), and shows the reasoning trail.

Stack

Layer	Tool	Role
Trusted context	Senso	Fee schedules, shipping rates, duty rules, banned items, user budget and sizes. Backs every cost line.
Task models	Superlinked SIE	Translation, field extraction from listings and photos, reranking
Memory	Cognee	Past purchases, rejected sellers, condition standards
Safety testing	Firedrill	Simulated checkout runs proving the agent stops at approval
Browser automation	Stagehand or Browser Use, on Browserbase	Navigates Buyee and fills the cart
Reasoning	Claude API	Final pick and explanation
Observability	Tokens&	Request timing and error traces
Optional	Statsig/Amplitude, Arcee	A/B two ranking strategies; cheap vs frontier model comparison

Sponsor prizes targeted: Senso, Superlinked (both prizes), Cognee and Firedrill, plus Statsig/Amplitude and Arcee if time allows.

Demo: request → recalled preferences → translated and ranked listings → cited landed-cost breakdown → cart filled and paused for approval, with the Firedrill report on screen.

## Agent chat (talk between our agents)

`agent-chat/relay.py` lets each of our agents send/receive messages through one small relay (Python stdlib only, no install).

Host (one person):
```
python3 agent-chat/relay.py serve --port 8787 --token <shared-secret>
```
Everyone (agent or human):
```
export RELAY_URL=http://<host-ip>:8787 RELAY_TOKEN=<shared-secret> RELAY_NAME=<your-name>
python3 agent-chat/relay.py agents           # who is here
python3 agent-chat/relay.py send <name> "hi"  # direct
python3 agent-chat/relay.py send all "hi"     # broadcast
python3 agent-chat/relay.py inbox             # read
python3 agent-chat/relay.py listen            # stream new messages
```
Plain HTTP: do not send secrets over venue Wi-Fi.

## Code layout (base framework)

Offline by default (no paid / live calls unless `SHOPPER_LIVE=1`). Python 3.10+, stdlib only.

```
python3 -m shopper "Fujifilm X100V" --budget 900 --ship-to US-CA   # demo flow, stops for approval
python3 -m unittest discover -s tests -t .                         # tests
```

| Module | Interface (`shopper/interfaces.py`) | Base impl | Real target |
|---|---|---|---|
| `shopper/listings` | `ListingSource.search` | fixtures/listings.json | Mercari / Yahoo Auctions + Superlinked extraction |
| `shopper/memory` | `MemoryStore` | local JSON | Cognee |
| `shopper/cost` | `CostEngine.landed_cost` (every line cited) | fixtures/rules.json (PLACEHOLDER numbers) | Senso + researched rules |
| `shopper/rank` | `Ranker.rank` | cheapest + condition | Superlinked rerank + Claude explanation |
| `shopper/checkout` | `Checkout.fill_cart` / `approve` | dry run + single-use approval token | Buyee via Browserbase, Firedrill runs |

Shared types are in `shopper/models.py`, the flow is in `shopper/pipeline.py`, and wiring is in `shopper/app.py`. Each module only touches its own folder, plus a line in `app.py` to swap in its implementation.
