# NOTES-api (backend API pane)

1. `python3 -m shopper.api --port 8000` — stdlib JSON API, CORS `*`: GET /health, POST /api/run, /api/approve, /api/reject, GET /api/memory?user= (`shopper/api/server.py`).
2. One `Deps` per server process, so approval tokens from /api/run survive until /api/approve; approval goes only through `deps.checkout.approve` (unknown / reused / forged token -> `blocked`, HTTP 200).
3. Extra gate: a cart whose seller the user rejected after the run is refused (`blocked`); on `placed` the listing is written to `past_purchases`.
4. Errors are `{"error":{"code","message"}}` with 400/404/405/413/500; frontend contract with real captured examples + curl lines in `docs/API.md`.
5. Tests: `tests/test_api.py` (14, real HTTP on a random port, temp memory file); negative controls run — forcing approve to `placed` turns 2 gate tests red, removing the rejected-seller check turns its test red.

## Requests to other panes (not changed by me)

- checkout: `Checkout` has no `cancel(token)`. The API keeps its own `revoked` set to refuse carts of a rejected seller; a `cancel` on the protocol would let checkout own that.
- models: `LandedCost.total_usd` is a property, so `asdict()` drops it. The API adds `picks[].cost.total_usd` itself (web/server.py does the same). A field or a `to_dict()` in models would remove the copy.
- web pane: `web/server.py` already serves a similar API on :8642 with a different contract (`token` instead of `approval_token`, `user_id` instead of `user`, reject re-runs the search). The frontend teammate should target `shopper.api` (docs/API.md); worth deciding whether web/ switches to it.
- rank / listings: with the 40-listing fixtures, `"Fujifilm X100V" --budget 900` now ranks a leather case (m007, $66) and an empty box (m008, $31) above any camera, and puts the case in the cart. The API passes this through unchanged; docs/API.md shows that real output.
