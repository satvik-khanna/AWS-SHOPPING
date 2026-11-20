# Shopper HTTP API (for the frontend)

Stdlib-only JSON API over the same pipeline as `python3 -m shopper`. Offline by default
(fixtures, dry-run checkout, nothing is ever paid). Every example below is real output
captured from this server on 2026-11-20.

```
python3 -m shopper.api --port 8000          # http://127.0.0.1:8000
python3 -m shopper.api --host 0.0.0.0 --port 8000   # reachable from another machine
```

- **CORS**: every response has `Access-Control-Allow-Origin: *`; `OPTIONS` preflight returns 204
  (`Allow-Methods: GET, POST, OPTIONS`, `Allow-Headers: Content-Type`). Call it from any origin.
- **Bodies**: `POST` bodies are JSON objects, `Content-Type: application/json`, max 64 KB.
- **State**: one server process keeps one set of dependencies alive, so the `approval_token`
  from `/api/run` stays valid for a later `/api/approve` (until the server restarts).
  Memory (preferences) is a JSON file in `SHOPPER_DATA_DIR` (default `.shopper-data/`).
- **User**: `user` is any string you choose (no sign-up). Omitted/empty means `"default"`.
  `user_id` is accepted as an alias.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/health` | — | server status + wiring |
| POST | `/api/run` | `{query, budget, ship_to?, user?, condition_min?}` | `PipelineResult` |
| POST | `/api/approve` | `{approval_token}` | `CartResult` |
| POST | `/api/reject` | `{user?, seller}` | `Preferences` (updated) |
| GET | `/api/memory?user=` | query `user` | `Preferences` |

## GET /health

```
curl -s http://127.0.0.1:8000/health
```
```json
{
  "ok": true,
  "version": "1",
  "live": false,
  "wiring": {
    "memory": "JsonMemory",
    "source": "FixtureSource",
    "cost": "RulesCostEngine",
    "ranker": "PriceRanker",
    "checkout": "DryRunCheckout"
  }
}
```
`live` is true only when the server runs with `SHOPPER_LIVE=1`. `wiring` names the implementation of each module.

## POST /api/run

Runs recall → search → landed cost → rank → fill cart. **Stops at `awaiting_approval`; never places an order.**

| field | type | required | notes |
|---|---|---|---|
| `query` | string | yes | free text, 1–300 chars |
| `budget` | number (USD) | yes | max **landed** total, > 0 (numeric string also accepted) |
| `ship_to` | string | no | default `"US-CA"` |
| `user` | string | no | default `"default"` |
| `condition_min` | string \| null | no | `new` \| `like_new` \| `good` \| `fair` |

```
curl -s -X POST http://127.0.0.1:8000/api/run -H 'Content-Type: application/json' \
  -d '{"query":"Fujifilm X100V","budget":900,"ship_to":"US-CA","user":"alice","condition_min":null}'
```

Response (real; the second pick is cut here for length, the real response had 3 picks):
```json
{
  "request": {
    "query": "Fujifilm X100V",
    "max_total_usd": 900.0,
    "ship_to": "US-CA",
    "user_id": "alice",
    "condition_min": null
  },
  "preferences": {
    "user_id": "alice",
    "sizes": {},
    "condition_min": null,
    "rejected_sellers": [],
    "past_purchases": [],
    "notes": []
  },
  "picks": [
    {
      "listing": {
        "id": "mercari:m007",
        "source": "mercari",
        "url": "https://jp.mercari.com/item/m007",
        "title_ja": "X100V 専用 純正レザーケース LC-X100V ブラック",
        "title_en": "genuine leather case LC-X100V black for X100V",
        "price_jpy": 6800,
        "condition": "good",
        "seller": "parts_jp",
        "ships_abroad": false,
        "weight_kg": 0.3,
        "category": "camera_accessory",
        "fields": {
          "item_type": "accessory",
          "color": "black",
          "condition_ja": "目立った傷や汚れなし",
          "buyee_url": "https://buyee.jp/mercari/item/m007"
        },
        "photos": []
      },
      "cost": {
        "listing_id": "mercari:m007",
        "lines": [
          {
            "label": "item price",
            "amount_usd": 45.33,
            "citation": {
              "source": "fixtures/rules.json#fx_jpy_per_usd",
              "as_of": "PLACEHOLDER",
              "url": null
            }
          },
          {
            "label": "proxy service fee",
            "amount_usd": 3.33,
            "citation": {
              "source": "fixtures/rules.json#proxy_service_fee_jpy",
              "as_of": "PLACEHOLDER",
              "url": null
            }
          },
          {
            "label": "domestic shipping",
            "amount_usd": 5.33,
            "citation": {
              "source": "fixtures/rules.json#domestic_shipping_jpy",
              "as_of": "PLACEHOLDER",
              "url": null
            }
          },
          {
            "label": "international shipping",
            "amount_usd": 10.0,
            "citation": {
              "source": "fixtures/rules.json#intl_shipping_jpy_per_kg",
              "as_of": "PLACEHOLDER",
              "url": null
            }
          },
          {
            "label": "duty",
            "amount_usd": 2.27,
            "citation": {
              "source": "fixtures/rules.json#duty_rate_by_category",
              "as_of": "PLACEHOLDER",
              "url": null
            }
          }
        ],
        "fx_jpy_per_usd": 150.0,
        "blocked_reason": null,
        "total_usd": 66.26
      },
      "score": 1.1264,
      "reason": "$66.26 landed ($834 under budget), condition good"
    }
  ],
  "cart": {
    "status": "awaiting_approval",
    "listing_id": "mercari:m007",
    "approval_token": "LPPmXsd8nbU",
    "cart_url": null,
    "detail": "dry run: would add https://jp.mercari.com/item/m007 to Buyee cart"
  },
  "trail": [
    "recalled preferences for alice: 0 past purchases, 0 rejected sellers",
    "found 9 listings",
    "skip mercari:m003: landed $1426.66 > budget $900.0",
    "skip mercari:m004: banned category: battery_loose",
    "pick mercari:m007 $66.26 score=1.13: $66.26 landed ($834 under budget), condition good",
    "pick mercari:m008 $31.26 score=1.07: $31.26 landed ($869 under budget), condition fair",
    "pick yahoo_auctions:y006 $437.99 score=0.51: $437.99 landed ($462 under budget), condition junk",
    "cart awaiting_approval for mercari:m007"
  ]
}
```

How to render it:
- `picks` is best-first (max 3). Each pick has `listing` (show `title_en`, `title_ja`, `url`, `condition`, `seller`),
  `cost.lines` (one row per line: `label`, `amount_usd`, and `citation.source` / `citation.as_of` as the "where this number came from"),
  `cost.total_usd` (sum of lines, rounded to cents), `score`, and `reason` (one-line why).
- `cart` is for `picks[0]`. Show an **Approve** button with `cart.approval_token`. `cart` is `null` and `picks` is `[]` when nothing fits the budget
  (the `trail` then ends with `"no candidate within budget"`).
- `trail` is the step-by-step log; show it as-is.
- `preferences` is memory as it was **before** this run (rejected sellers are already filtered out of `picks`).
- `as_of: "PLACEHOLDER"` comes from the offline cost rules (fixtures); real dates arrive when the cost module ships them.

## POST /api/approve

The **only** way to get `status: "placed"`. Each token works once.

```
curl -s -X POST http://127.0.0.1:8000/api/approve -H 'Content-Type: application/json' \
  -d '{"approval_token":"LPPmXsd8nbU"}'
```
First use (real):
```json
{
  "status": "placed",
  "listing_id": "mercari:m007",
  "approval_token": null,
  "cart_url": null,
  "detail": "dry run: order not actually placed"
}
```
Same token again, or a token the server never issued (real; HTTP status is still **200** — check `status`):
```json
{
  "status": "blocked",
  "listing_id": "",
  "approval_token": null,
  "cart_url": null,
  "detail": "unknown or already used approval token"
}
```
If the user rejected the cart's seller after the run, approval is also refused with
`status: "blocked"`, `detail: "cart seller was rejected; run the search again"`.
On `placed`, the listing id is added to the user's `past_purchases`. In offline mode
`placed` is a dry run (`detail` says so); nothing is bought.

`status` values: `awaiting_approval` (from /api/run), `placed`, `blocked`, `failed`.

## POST /api/reject

Remember "never show me this seller again" for a user. Idempotent. Returns the updated preferences;
call `/api/run` again to get new picks without that seller. Optionally pass `approval_token` (and `reason`) to also reject the pending checkout session on the state machine.

```
curl -s -X POST http://127.0.0.1:8000/api/reject -H 'Content-Type: application/json' \
  -d '{"user":"alice","seller":"parts_jp","approval_token":"LPPmXsd8nbU","reason":"scratches"}'
```
```json
{
  "user_id": "alice",
  "sizes": {},
  "condition_min": null,
  "rejected_sellers": [
    "parts_jp"
  ],
  "past_purchases": [
    "mercari:m007"
  ],
  "notes": []
}
```

## GET /api/sessions/<id>

Returns the timestamped checkout state-machine log, prompt-injection warnings, and current state for a session (`cart.session_id` from `/api/run`). Returns `404` if the session id does not exist.

```
curl -s 'http://127.0.0.1:8000/api/sessions/<session_id>'
```

## GET /api/memory?user=

```
curl -s 'http://127.0.0.1:8000/api/memory?user=alice'
```
```json
{
  "user_id": "alice",
  "sizes": {},
  "condition_min": null,
  "rejected_sellers": [
    "parts_jp"
  ],
  "past_purchases": [
    "mercari:m007"
  ],
  "notes": []
}
```
A user never seen before returns empty lists (not 404).

## Errors

Errors are JSON with a matching HTTP status: `{"error": {"code": "...", "message": "..."}}`.
Show `message` to the user; it says what to fix.

| status | code | when |
|---|---|---|
| 400 | `bad_request` | missing/invalid field, body not JSON, body not an object |
| 404 | `not_found` | unknown path |
| 405 | `method_not_allowed` | e.g. `GET /api/run` |
| 413 | `too_large` | body over 64 KB |
| 500 | `internal` | a module raised; message has the exception |

Real examples:
```
$ curl -s -X POST http://127.0.0.1:8000/api/run -H 'Content-Type: application/json' -d '{"budget":900}'
{"error": {"code": "bad_request", "message": "query is required (non-empty string)"}}   # HTTP 400

$ curl -s -X POST http://127.0.0.1:8000/api/run -d '{bad'
{"error": {"code": "bad_request", "message": "body is not valid JSON"}}                  # HTTP 400

$ curl -s http://127.0.0.1:8000/api/nope
{"error": {"code": "not_found", "message": "no endpoint /api/nope; see docs/API.md"}}    # HTTP 404
```

## Minimal fetch example

```js
const API = "http://127.0.0.1:8000";
const post = (path, body) => fetch(API + path, {method: "POST",
  headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})
  .then(async r => { const j = await r.json(); if (!r.ok) throw new Error(j.error.message); return j; });

const result = await post("/api/run", {query: "Fujifilm X100V", budget: 900, user: "alice"});
if (result.cart) {
  const cart = await post("/api/approve", {approval_token: result.cart.approval_token});
  // cart.status === "placed" | "blocked"
}
```

