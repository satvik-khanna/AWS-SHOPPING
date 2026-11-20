1. Works: Buyee cart state machine (open listing -> add to cart -> cart filled -> STOP awaiting_approval -> approve -> placed) with a timestamped step log; random single-use tokens (stored only as sha256, 30-min TTL, atomic claim on disk); preflight guards (over budget, blocked item, bad numbers) and prompt-injection flags on listing text (shown as warnings, never acted on); memory of purchases / rejected sellers / condition_min / sizes / notes, updated by approve and reject.
2. Run: `python3 -m shopper buy "Fujifilm X100V" --budget 900` (prints step log + token), then `python3 -m shopper approve <token>` or `reject <token> --reason "scratches" --seller`; `python3 -m shopper sessions | log <id> | memory --size shoes=27cm | firedrill -n 130`. Tests: `python3 -m unittest discover -s tests -t .`.
3. Firedrill: `python3 -m firedrill.run` runs 130 simulated checkouts across 13 scenarios (forged / reused / mutated / expired / cross-session tokens, injected "buy now" listing text, over-budget pick, reject-then-approve, 8-thread approve race, driver errors, direct driver.place / grant forging) and writes firedrill/out/report.md: 130/130 held. Negative control: a planted auto-approve on flagged listings turned it to 120/130 and failed a unit test.
4. Fake: the Buyee driver is simulated by default (no network, "placed" = a simulated order ref); the Buyee item URL patterns are unverified (buyee.jp answered 403 to curl). The live Playwright driver (`SHOPPER_LIVE=1` + `BUYEE_STORAGE_STATE=<storage_state.json saved after you log in yourself>` + `pip install playwright`) has never been run against the real site; it never logs in, refuses auction bids, and on approve it hands the cart URL back to you instead of paying. The Cognee mirror (`SHOPPER_LIVE=1` + `COGNEE_API_KEY`/`LLM_API_KEY` + `cognee` package) is also untested.
5. Wiring: `shopper.checkout.DryRunCheckout` (already used by app.py) is now the state machine with file-backed sessions under `$SHOPPER_DATA_DIR/checkout` and memory feedback; the only edit outside my folders is one import line in `shopper/__main__.py`.

## Requests to the owners of shared files

- ~~pipeline.py: pass the budget to fill_cart~~ done in 7478f1d (`fill_cart(pick, budget_usd=, user_id=)`, pipeline passes them when the signature accepts them).
- models.py: `Listing` has no description field, so injection scanning only covers title / seller / url / fields. If the listings pane adds `description_ja`, the checkout guard should scan it too.
- `CartResult` has no session id; it is carried as a `[session <id>]` prefix in `detail`. A `session_id` field would be cleaner for the API pane (`GET /sessions/<id>` step log).
- interfaces.py: `Checkout` has no `reject`; `BuyeeCheckout.reject(token, reason, reject_seller)` exists and the API pane can call it.

## Layout

- `shopper/checkout/machine.py`: states, allowed transitions, sealed ApprovalGrant (only `approve()` can mint one)
- `shopper/checkout/core.py`: BuyeeCheckout (fill_cart / approve / reject / session)
- `shopper/checkout/drivers.py`: SimBuyeeDriver, LiveBuyeeDriver (Playwright), default_driver()
- `shopper/checkout/guards.py`: preflight checks and injection scanner; `store.py`: in-memory and file stores with atomic claim
- `shopper/memory/`: JsonMemory (+ set_condition_min / set_size / add_note), feedback.py, cognee_adapter.py
- `shopper/cli_checkout.py`, `firedrill/run.py`
