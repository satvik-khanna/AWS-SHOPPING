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
| POST | `/api/chat` | `{user?, message, session?, payment_method?}` | `{reply, components, trail}` |
| POST | `/api/buy` | `/api/run` body + `listing_id` (+ `session?`) | PipelineResult + `cua` + `components` |

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

## POST /api/chat — the agent answers with UI components

The chat box sends one message; the agent answers with a short `reply` plus a list of typed
`components` to render inside the chat. Intent parsing is rule-based offline (no LLM unless
`SHOPPER_LIVE=1`). Keep the returned `session` and send it back on the next message.

| field | type | required | notes |
|---|---|---|---|
| `message` | string | yes | 0–500 chars. Empty or `help` -> help notice |
| `user` | string | no | default `"default"`; a session belongs to one user (other user -> 403) |
| `session` | string | no | from the previous reply; unknown/expired -> a new session + a notice |
| `payment_method` | object | no | **masked only**: `{brand, last4, exp:"MM/YY", provider_token?}`. Full card numbers, CVV or any other field -> 400. The token is never echoed back |
| `budget`, `ship_to`, `condition_min` | | no | defaults if the message does not say them (budget default $1000) |
| `open_real` | bool | no | ask to open the real site in Chrome on Buy; **only honoured when the server runs with `SHOPPER_LIVE=1`** |

Response: `{version, session, user, intent, reply, components:[...], trail:[...]}`.

| what the user types (examples) | intent | components |
|---|---|---|
| `list up Fujifilm X100V under $900`, `find a Ricoh GR III below 650 shipped to US-NY` | `search` | `listing_table` (+ `notice` when nothing fits) |
| `list up` / `show the list` | `list` | `listing_table` (same session) |
| `sort by price` / `sort by price desc` / `sort by condition` / `sort by relevance` / `cheapest first` | `sort` | `listing_table` re-sorted |
| `buy 2` (row number in the table as currently sorted) / `buy m005` / `buy mercari:m005` | `buy` | `agent_browser` + `swipe_to_pay` + `cost_breakdown` |
| `approve`, `pay` | `approve` | `notice` only — **typing never pays**; only the swipe (`POST /api/approve`) does |
| `cancel`, `reject`, `skip` | `reject` | `notice` + `listing_table`; the pending cart token is revoked |

A new search or a new Buy in the same session revokes the previous pending token (approving it returns `blocked`).
Listing never creates a cart: a cart and an `approval_token` exist only after Buy.

```
curl -s -X POST http://127.0.0.1:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"user":"alice","message":"list up Fujifilm X100V under $900","payment_method":{"brand":"Visa","last4":"4242","exp":"12/29"}}'
```
Real response (cut to 1 of 5 rows and the trail shortened):
```json
{
  "version": 1,
  "session": "chat_AwQYt4SXoxsm",
  "user": "alice",
  "intent": "search",
  "reply": "Found 5 for \"Fujifilm X100V\" within $900 landed to US-CA, best match first. Press Buy on a row, or say \"sort by price\".",
  "components": [
    {
      "type": "listing_table",
      "version": 1,
      "session": "chat_AwQYt4SXoxsm",
      "query": "Fujifilm X100V",
      "budget_usd": 900.0,
      "columns": [
        {
          "key": "title",
          "label": "Item",
          "sortable": false
        },
        {
          "key": "price",
          "field": "total_usd",
          "label": "Landed (USD)",
          "sortable": true
        },
        {
          "key": "condition",
          "field": "condition",
          "label": "Condition",
          "sortable": true
        },
        {
          "key": "relevance",
          "field": "relevance_rank",
          "label": "Best match",
          "sortable": true
        },
        {
          "key": "source",
          "label": "Source",
          "sortable": false
        },
        {
          "key": "seller",
          "label": "Seller",
          "sortable": false
        }
      ],
      "sort": {
        "key": "relevance",
        "dir": "asc",
        "options": [
          "relevance",
          "price",
          "condition"
        ],
        "chat": "sort by price | sort by condition | sort by relevance"
      },
      "rows": [
        {
          "id": "mercari:m001",
          "relevance_rank": 1,
          "title": "Fujifilm X100V silver excellent condition",
          "title_ja": "富士フイルム X100V シルバー 美品",
          "total_usd": 874.14,
          "price_jpy": 118000,
          "condition": "like_new",
          "source": "mercari",
          "seller": "camera_taro",
          "url": "https://jp.mercari.com/item/m001",
          "image": null,
          "score": 0.6168,
          "reason": "matches Fujifilm, x100v; $874 landed, $26 under budget; condition like new (seller grade: 未使用に近い); tested working; low shutter count (3,200)",
          "actions": [
            {
              "type": "buy",
              "label": "Buy",
              "method": "POST",
              "path": "/api/buy",
              "body": {
                "query": "Fujifilm X100V",
                "budget": 900.0,
                "ship_to": "US-CA",
                "user": "alice",
                "condition_min": null,
                "listing_id": "mercari:m001",
                "session": "chat_AwQYt4SXoxsm"
              }
            },
            {
              "type": "reject_seller",
              "label": "Never show camera_taro",
              "method": "POST",
              "path": "/api/reject",
              "body": {
                "user": "alice",
                "seller": "camera_taro"
              }
            }
          ]
        }
      ]
    }
  ],
  "trail": [
    "intent: search (rule-based)",
    "recalled preferences for alice: 0 past purchases, 0 rejected sellers",
    "...",
    "ranker: ClaudeRerank [offline ranking] (pipeline order); Superlinked SIE off (offline)",
    "no cart yet: waiting for the user to press Buy on a row"
  ]
}
```

```
curl -s -X POST http://127.0.0.1:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"user":"alice","session":"chat_AwQYt4SXoxsm","message":"sort by price"}'
```
Real response (rows cut to the sort-relevant fields):
```json
{
  "version": 1,
  "session": "chat_AwQYt4SXoxsm",
  "user": "alice",
  "intent": "sort",
  "reply": "Sorted by price (low to high).",
  "components": [
    {
      "type": "listing_table",
      "version": 1,
      "session": "chat_AwQYt4SXoxsm",
      "query": "Fujifilm X100V",
      "budget_usd": 900.0,
      "columns": [
        {
          "key": "title",
          "label": "Item",
          "sortable": false
        },
        {
          "key": "price",
          "field": "total_usd",
          "label": "Landed (USD)",
          "sortable": true
        },
        {
          "key": "condition",
          "field": "condition",
          "label": "Condition",
          "sortable": true
        },
        {
          "key": "relevance",
          "field": "relevance_rank",
          "label": "Best match",
          "sortable": true
        },
        {
          "key": "source",
          "label": "Source",
          "sortable": false
        },
        {
          "key": "seller",
          "label": "Seller",
          "sortable": false
        }
      ],
      "sort": {
        "key": "price",
        "dir": "asc",
        "options": [
          "relevance",
          "price",
          "condition"
        ],
        "chat": "sort by price | sort by condition | sort by relevance"
      },
      "rows": [
        {
          "id": "yahoo_auctions:y006",
          "total_usd": 480.22,
          "condition": "junk",
          "relevance_rank": 5,
          "...": "..."
        },
        {
          "id": "mercari:m009",
          "total_usd": 712.35,
          "condition": "fair",
          "relevance_rank": 4,
          "...": "..."
        },
        {
          "id": "yahoo_auctions:y002",
          "total_usd": 733.46,
          "condition": "fair",
          "relevance_rank": 3,
          "...": "..."
        },
        {
          "id": "mercari:m005",
          "total_usd": 816.46,
          "condition": "good",
          "relevance_rank": 2,
          "...": "..."
        },
        {
          "id": "mercari:m001",
          "total_usd": 874.14,
          "condition": "like_new",
          "relevance_rank": 1,
          "...": "..."
        }
      ]
    }
  ],
  "trail": [
    "intent: sort (rule-based)",
    "sorted 5 rows by price asc"
  ]
}
```

```
curl -s -X POST http://127.0.0.1:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"user":"alice","session":"chat_AwQYt4SXoxsm","message":"buy 2"}'
```
Real response (full):
```json
{
  "version": 1,
  "session": "chat_AwQYt4SXoxsm",
  "user": "alice",
  "intent": "buy",
  "reply": "Cart ready for FUJIFILM X100V silver some scratches slight lens haze at $712.35 landed. Nothing is paid yet: swipe to pay to confirm.",
  "components": [
    {
      "type": "agent_browser",
      "version": 1,
      "status": "cart_ready",
      "listing_id": "mercari:m009",
      "opened_url": "https://buyee.jp/mercari/item/m009",
      "listing_url": "https://jp.mercari.com/item/m009",
      "cart_url": "https://buyee.jp/cart#fake-08ad8446",
      "opened_in_browser": false,
      "driver": "fake-chrome",
      "checkout_session": "08ad8446f9fc5690",
      "steps": [
        {
          "seq": 1,
          "at": 1795216912.024649,
          "state": "created",
          "actor": "agent",
          "action": "start",
          "detail": "mercari:m009 landed $712.35 for user alice"
        },
        {
          "seq": 2,
          "at": 1795216912.030377,
          "state": "listing_opened",
          "actor": "agent",
          "action": "open new Chrome tab on listing",
          "detail": "https://buyee.jp/mercari/item/m009"
        },
        {
          "seq": 3,
          "at": 1795216912.0334542,
          "state": "added_to_cart",
          "actor": "agent",
          "action": "add to cart",
          "detail": "mercari:m009"
        },
        {
          "seq": 4,
          "at": 1795216912.033537,
          "state": "cart_filled",
          "actor": "agent",
          "action": "cart filled",
          "detail": "1 item(s) at https://buyee.jp/cart#fake-08ad8446"
        },
        {
          "seq": 5,
          "at": 1795216912.041038,
          "state": "cart_filled",
          "actor": "agent",
          "action": "vault: remember sign-in",
          "detail": "buyee.jp"
        },
        {
          "seq": 6,
          "at": 1795216912.0410628,
          "state": "checkout_opened",
          "actor": "agent",
          "action": "go to checkout page",
          "detail": "https://buyee.jp/checkout#fake-08ad8446"
        },
        {
          "seq": 7,
          "at": 1795216912.0421472,
          "state": "shipping_filled",
          "actor": "agent",
          "action": "fill shipping",
          "detail": "address_line1, city, country, full_name, postal_code, region from offline demo placeholder (no saved address)"
        },
        {
          "seq": 8,
          "at": 1795216912.042177,
          "state": "shipping_filled",
          "actor": "agent",
          "action": "select saved payment method",
          "detail": "site default payment method (no card number or CVV typed)"
        },
        {
          "seq": 9,
          "at": 1795216912.043045,
          "state": "awaiting_swipe",
          "actor": "agent",
          "action": "STOP at payment step: waiting for the user's swipe",
          "detail": "$712.35; token bound to alice, expires in 1800s"
        }
      ],
      "warnings": [],
      "detail": "[session 08ad8446f9fc5690] [awaiting_swipe] checkout filled on fake-chrome up to the payment step; nothing paid. Swipe (approve) to place"
    },
    {
      "type": "swipe_to_pay",
      "version": 1,
      "approval_token": "LNa_e4narACIsnWGdFzYow",
      "listing_id": "mercari:m009",
      "title": "FUJIFILM X100V silver some scratches slight lens haze",
      "amount_usd": 712.35,
      "currency": "USD",
      "payment_method": {
        "brand": "Visa",
        "last4": "4242",
        "exp": "12/29",
        "label": "Visa •••• 4242",
        "has_provider_token": false
      },
      "expires_at": 1795218712.0424042,
      "expires_at_iso": "2026-11-20T23:51:52+00:00",
      "approve": {
        "method": "POST",
        "path": "/api/approve",
        "body": {
          "approval_token": "LNa_e4narACIsnWGdFzYow"
        }
      },
      "reject": {
        "method": "POST",
        "path": "/api/reject",
        "body": {
          "user": "alice",
          "seller": "cam_recycle_55",
          "approval_token": "LNa_e4narACIsnWGdFzYow",
          "reason": "declined at swipe"
        }
      },
      "label": "Slide to pay $712.35"
    },
    {
      "type": "cost_breakdown",
      "version": 1,
      "listing_id": "mercari:m009",
      "title": "FUJIFILM X100V silver some scratches slight lens haze",
      "lines": [
        {
          "label": "item price (JPY 95,000 @ 159.93)",
          "amount_usd": 594.01,
          "citation": {
            "source": "fixtures/rules.json#fx_jpy_per_usd",
            "as_of": "2026-11-13",
            "url": "https://www.federalreserve.gov/releases/h10/current/"
          }
        },
        {
          "label": "Buyee service fee",
          "amount_usd": 3.13,
          "citation": {
            "source": "fixtures/rules.json#buyee_service_fee_jpy",
            "as_of": "2026-11-20",
            "url": "https://buyee.jp/helpcenter/guide/fees"
          }
        },
        {
          "label": "Buyee standard plan",
          "amount_usd": 3.13,
          "citation": {
            "source": "fixtures/rules.json#buyee_plan_fee_jpy.standard",
            "as_of": "2026-11-20",
            "url": "https://buyee.jp/helpcenter/guide/fees"
          }
        },
        {
          "label": "payment fee",
          "amount_usd": 0.0,
          "citation": {
            "source": "fixtures/rules.json#payment_fee_jpy",
            "as_of": "2026-11-20",
            "url": "https://buyee.jp/helpcenter/guide/fees"
          }
        },
        {
          "label": "domestic shipping (JP)",
          "amount_usd": 4.69,
          "citation": {
            "source": "fixtures/rules.json#domestic_shipping_jpy",
            "as_of": "2026-11-20",
            "url": "https://help.jp.mercari.com/guide/articles/136/"
          }
        },
        {
          "label": "international shipping Japan Post EMS 1.00 kg (4-8 days)",
          "amount_usd": 33.14,
          "citation": {
            "source": "fixtures/rules.json#intl_shipping_us.ems",
            "as_of": "2026-11-20",
            "url": "https://www.post.japanpost.jp/send/oversea/charge/list-ems/all_en.html"
          }
        },
        {
          "label": "US duty MFN camera HTS 8525.89.40.00 (de minimis suspended)",
          "amount_usd": 0.0,
          "citation": {
            "source": "fixtures/rules.json#us_duty_mfn_by_category.camera",
            "as_of": "2026-11-20",
            "url": "https://hts.usitc.gov/"
          }
        },
        {
          "label": "US Japan 12.5% floor top-up (HTS 9903.05.49)",
          "amount_usd": 74.25,
          "citation": {
            "source": "fixtures/rules.json#us_japan_floor_rate",
            "as_of": "2026-11-20",
            "url": "https://hts.usitc.gov/reststop/search?keyword=9903.05.49"
          }
        }
      ],
      "total_usd": 712.35,
      "fx_jpy_per_usd": 159.93
    }
  ],
  "trail": [
    "intent: buy (rule-based)",
    "cart awaiting_approval for mercari:m009 (stopped: waiting for the swipe)"
  ]
}
```
Then the swipe calls `swipe_to_pay.approve` as-is (real):
```
curl -s -X POST http://127.0.0.1:8000/api/approve -H 'Content-Type: application/json' -d '{"approval_token":"<swipe_to_pay.approval_token>"}'
```
```json
{
  "status": "placed",
  "listing_id": "mercari:m009",
  "approval_token": null,
  "cart_url": "https://buyee.jp/cart#fake-08ad8446",
  "detail": "[session 08ad8446f9fc5690] [placed] simulated order FAKE-08AD8446; no real money moved",
  "session_id": "08ad8446f9fc5690"
}
```

## Components (version 1)

Every component is `{"type": "<name>", "version": 1, ...}`. Unknown types: ignore. Fields are only added, never renamed, within a version.

| type | fields |
|---|---|
| `listing_table` | `session`, `query`, `budget_usd`, `columns[]` (`key`, `field`, `label`, `sortable`), `sort` (`key`: relevance\|price\|condition, `dir`: asc\|desc, `options`, `chat`), `rows[]` |
| row | `id`, `relevance_rank`, `title`, `title_ja`, `total_usd` (landed), `price_jpy`, `condition`, `source`, `seller`, `url`, `image` (null offline), `score`, `reason`, `actions[]` |
| row action | `{type:"buy", method:"POST", path:"/api/buy", body}` — send `body` unchanged; `{type:"reject_seller", path:"/api/reject", body}` |
| `cost_breakdown` | `listing_id`, `title`, `lines[]` (`label`, `amount_usd`, `citation{source, as_of, url}`), `total_usd`, `fx_jpy_per_usd` |
| `agent_browser` | `status` (cart_ready\|placed\|blocked\|failed), `listing_id`, `opened_url`, `listing_url`, `cart_url`, `opened_in_browser` (true only when Chrome really opened, live mode), `driver`, `checkout_session` (for `GET /api/sessions/<id>`), `steps[]` (`seq`, `at` epoch s, `state`, `actor`, `action`, `detail`), `warnings[]`, `detail` |
| `swipe_to_pay` | `approval_token`, `listing_id`, `title`, `amount_usd`, `currency`, `payment_method` (`brand`, `last4`, `exp`, `label`, `has_provider_token`) or null, `expires_at` (epoch s), `expires_at_iso`, `approve` / `reject` (ready-made `{method, path, body}`), `label` |
| `notice` | `level` (info\|warn\|error), `text` |

Sorting on the client: sort `rows` by the column's `field` locally, or send `sort by <key>` to get the server order (same result).

## POST /api/buy — same contract as web/server.py

Body = the `/api/run` body plus `listing_id` (and optionally `session`, `open_real`, `payment_method`).
`user_id` and `user` are both accepted. Without `session` the search is re-run and only the chosen
listing gets a cart; with the `session` from a chat `listing_table` (the row's buy action), the table's items are used as-is.
The listing must be in the results within budget, otherwise 404.

```
curl -s -X POST http://127.0.0.1:8000/api/buy -H 'Content-Type: application/json' \
  -d '{"query":"Fujifilm X100V","budget":900,"ship_to":"US-CA","user_id":"alice","condition_min":null,"listing_id":"mercari:m005","open_real":true}'
```
Real response (picks / components shortened):
```json
{
  "request": "... same as /api/run ...",
  "preferences": "... same as /api/run ...",
  "picks": [
    "... 5 picks, same shape as /api/run ..."
  ],
  "cart": {
    "status": "awaiting_approval",
    "listing_id": "mercari:m005",
    "approval_token": "zYb3fUlgNWg9JPwcPG83Hw",
    "cart_url": "https://buyee.jp/cart#fake-a86b3c48",
    "detail": "[session a86b3c48d7a4be1e] [awaiting_swipe] checkout filled on fake-chrome up to the payment step; nothing paid. Swipe (approve) to place",
    "session_id": "a86b3c48d7a4be1e"
  },
  "trail": [
    "recalled preferences for alice: 1 past purchases, 0 rejected sellers",
    "...",
    "cart awaiting_approval for mercari:m005 (stopped: waiting for the swipe)"
  ],
  "cua": {
    "opened_in_chrome": false,
    "skipped": "offline (SHOPPER_LIVE is not 1): no browser opened"
  },
  "components": [
    "... agent_browser, swipe_to_pay, cost_breakdown: same shapes as in the chat buy example ..."
  ]
}
```
`cua` says why no browser opened (offline). With `SHOPPER_LIVE=1` and `open_real:true` it reports the don-tacos Chrome window (`opened_in_chrome`, `opened_url`).

Chat / buy errors (real):
```
$ curl -s -X POST http://127.0.0.1:8000/api/chat -H 'Content-Type: application/json' -d '{"user":"alice","message":"list up","payment_method":{"brand":"Visa","last4":"4242424242424242","exp":"12/29"}}'
{"error": {"code": "bad_request", "message": "payment_method.last4 must be exactly the last 4 digits (never the full number)"}}   # HTTP 400

$ curl -s -X POST http://127.0.0.1:8000/api/chat -H 'Content-Type: application/json' -d '{"user":"mallory","session":"chat_AwQYt4SXoxsm","message":"buy 1"}'
{"error": {"code": "forbidden", "message": "this chat session belongs to another user"}}   # HTTP 403
```

### Ranking engine and Superlinked

Offline, the list keeps the pipeline ranker's order and the trail says so
(`ranker: ... ; Superlinked SIE off (offline)`). With `SHOPPER_LIVE=1` and `SIE_API_KEY` set, the chat list is
reranked by Superlinked SIE through `backend/kai/integrations/sie.py`; the trail line then reads
`ranker: Superlinked SIE rerank (<model>) over <ranker>`. If SIE fails, the pipeline order is kept and the trail
gives the error. If the pipeline ranker already used SIE, it is not called a second time.

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

