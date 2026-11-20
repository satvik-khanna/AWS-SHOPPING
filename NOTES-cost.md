# NOTES-cost

## P1 payments (2026-11-20) — summary
1. Works: `shopper/payments` = PaymentProvider (interfaces.py) for card (Stripe PaymentMethod token), stripe, paypal (Orders v2), venmo (PayPal Orders v2 + JS SDK button). `build_provider(kind)`; offline fake by default.
2. Guards (all kinds): confirm() needs the checkout ApprovalGrant the approval gate recorded for that cart session; amount must equal the approved cart's landed total to the cent; same idempotency key -> same intent; one paid intent per cart (double swipe returns the first result, no second charge); provider gets Idempotency-Key / PayPal-Request-Id. Ledger = `$SHOPPER_DATA_DIR/payments/ledger.json` (flock).
3. Run: `python3 -m unittest tests.test_cost_payments` (15 tests; 9 mutations, one per guard, each turns a test red). PaymentMethodRef is the shared vault type (token, brand, last4, exp, label) plus `kind`.
4. Fake: all charges offline (FakeBackend). Live Stripe/PayPal adapters are written against the docs below and unit-tested with a mocked HTTP opener only; never run against the real sandbox (no keys on this machine).
5. Keys the owner must issue (test/sandbox only): `STRIPE_SECRET_KEY` = Stripe sandbox secret `sk_test_...` (or restricted `rk_test_...` with PaymentIntents write + PaymentMethods read); optional `STRIPE_CUSTOMER_ID` (sandbox customer with saved cards). `PAYPAL_CLIENT_ID` + `PAYPAL_CLIENT_SECRET` from a PayPal Developer sandbox REST app (US business sandbox account, Venmo needs US + USD); optional `PAYPAL_CUSTOMER_ID` for vaulted methods. Live keys (sk_live_/rk_live_/pk_*, PAYPAL_ENV != sandbox) are refused.

### P1 docs checked 2026-11-20
- Stripe PaymentIntents create / confirm / cancel: https://docs.stripe.com/api/payment_intents/create , https://docs.stripe.com/api/payment_intents/confirm , https://docs.stripe.com/api/payment_intents/cancel (Basic auth secret key, form-encoded, amount in cents, `allowed_payment_method_types`, statuses; Stripe-Version shown 2026-11-18.endive, pinned)
- Stripe idempotency: https://docs.stripe.com/api/idempotent_requests (Idempotency-Key header, up to 255 chars, pruned after >=24h, reuse with different params errors)
- Stripe keys: https://docs.stripe.com/keys (sk_test_/rk_test_/pk_test_ vs *_live_) ; test tokens: https://docs.stripe.com/testing (pm_card_visa, pm_card_visa_chargeDeclined)
- Stripe saved methods: https://docs.stripe.com/api/payment_methods/customer_list
- Stripe has no Venmo payment method, and Stripe PayPal is only for businesses in listed European countries (https://docs.stripe.com/payments/paypal) -> PayPal and Venmo go through PayPal directly.
- PayPal OAuth: https://developer.paypal.com/api/rest/authentication/ (client_credentials, sandbox host api-m.sandbox.paypal.com)
- PayPal Orders v2: https://developer.paypal.com/docs/api/orders/v2/ (create intent CAPTURE, capture, payment_source.venmo / vault_id, statuses CREATED/APPROVED/COMPLETED/PAYER_ACTION_REQUIRED; no cancel endpoint, unapproved orders expire)
- PayPal idempotency: https://developer.paypal.com/api/rest/reference/idempotency/ (PayPal-Request-Id, max 38 chars)
- Venmo: https://developer.paypal.com/docs/checkout/pay-with-venmo/ (US merchants and consumers only, USD, JS SDK with enable-funding=venmo, buyer approves in Venmo app)
- PayPal vault: https://developer.paypal.com/docs/api/payment-tokens/v3/ (GET /v3/vault/payment-tokens?customer_id=)

### P1 needs from other panes (not my folders)
- checkout: `approve()` should call `build_provider(kind).confirm(intent, grant)` with the grant BEFORE `driver.place()`; today the grant is only visible inside approve()/driver.place().
- api / web: create the intent when the cart reaches awaiting_approval (amount = cart total, key = e.g. session_id + method id), then confirm on the swipe; PayPal/Venmo need the buyer approval (approve_url / Venmo JS SDK button) before confirm.

## Cost engine (earlier)

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
