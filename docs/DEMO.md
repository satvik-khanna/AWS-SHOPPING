# AWS-SHOPPING — 3-Minute Hackathon Demo Script

Offline-by-default Japan personal shopping agent: recalls preferences, translates/extracts Japanese marketplace listings, computes **cited landed cost** (every number backed by a source & `as_of` date), fills the Buyee proxy cart, and **stops for human approval** before any money moves.

---

## 0. Start Services (2 terminals, stdlib only — no install needed)

```bash
# Terminal A: Backend JSON API (for the frontend, CORS *)
python3 -m shopper.api --port 8000

# Terminal B: 1-URL AI Commerce Gateway (Link-in-Bio + MCP + UCP + ACP + llms.txt)
python3 -m shopper.gateway --port 8080
```

Quick health check:
```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

---

## 1. Scene 1 — Core Demo: "Used Fujifilm X100V under $900, shipped to California" (45s)

Show **trusted context** (real 2026-11-20 FX, Buyee fees, Japan Post EMS weight band, US HTS MFN + Japan 12.5% floor under HTS 9903.05.49 with de minimis suspended) and **banned-item guard** (loose lithium battery automatically blocked).

```bash
python3 -m shopper "Fujifilm X100V" --budget 900 --ship-to US-CA
```

What to point out on screen:
1. `skip mercari:m003`: over budget ($1,543 landed > $900).
2. `skip mercari:m004`: **banned** (`battery_loose` — standalone lithium batteries cannot ship internationally, citing `buyee.jp/helpcenter/guide/lithium-battery`).
3. `#1 mercari:m005` ($816.46 landed): every cost line has `[fixtures/rules.json#<rule> as of 2026-11-20]`, including the **US Japan 12.5% floor top-up ($85.82, HTS 9903.05.49)**.
4. Cart stops at **`awaiting_approval`** with a single-use approval token.

---

## 2. Scene 2 — Niche Japan Discovery Scenarios (45s)

Show why Japanese marketplaces + proxy matter for niche verticals (from `docs/survey-genres-and-rules.md`):

```bash
# A. Vintage apparel with size normalization (Kapital size 2 -> M vs tagged L)
python3 -m shopper "Kapital or vintage Champion Reverse Weave sweatshirt, size L" --budget 180 --ship-to US-CA

# B. Indie Japanese creator goods on BOOTH / BASE
python3 -m shopper "limited-edition risograph artbook or acrylic stand from Japanese indie artist on BOOTH/BASE" --budget 80 --ship-to US-CA

# C. Hard Off junk electronics ("repair bet" ranked above "parts only", with no-returns warning)
python3 -m shopper "junk Sony Walkman or Game Boy from Hard Off Net Mall" --budget 60 --ship-to US-CA
```

---

## 3. Scene 3 — Approval Gate, Step Log & Memory Feedback (45s)

Show the human-in-the-loop checkout state machine (`open listing -> add to cart -> cart filled -> STOP awaiting_approval`):

```bash
# 1. Run via API and capture token + session_id
curl -s -X POST http://127.0.0.1:8000/api/run \
  -H 'Content-Type: application/json' \
  -d '{"query":"Fujifilm X100V","budget":900,"ship_to":"US-CA","user":"demo"}' | python3 -m json.tool

# 2. Inspect the timestamped checkout step log & injection warnings
curl -s http://127.0.0.1:8000/api/sessions/<session_id> | python3 -m json.tool

# 3. Approve once -> status "placed" (second call with same token -> "blocked")
curl -s -X POST http://127.0.0.1:8000/api/approve \
  -H 'Content-Type: application/json' \
  -d '{"approval_token":"<token>"}' | python3 -m json.tool

# 4. Or reject a seller -> persisted in memory, future runs skip that seller
curl -s -X POST http://127.0.0.1:8000/api/reject \
  -H 'Content-Type: application/json' \
  -d '{"user":"demo","seller":"<seller>","approval_token":"<token>","reason":"scratches"}' | python3 -m json.tool
```

---

## 4. Scene 4 — Safety Proof: Firedrill 130-Run Stress Test (30s)

Prove the approval gate holds under adversarial conditions (forged/reused/expired/cross-session tokens, prompt-injection text inside listings, over-budget picks, 8-thread race conditions, direct driver forgery):

```bash
python3 -m firedrill.run
head -n 30 firedrill/out/report.md
```

Expected: **`130/130 runs held the approval gate (0 broke it)`**.

---

## 5. Scene 5 — Bonus: 1-URL AI Commerce Gateway (15s)

One URL exposes Link-in-Bio HTML + MCP + UCP + ACP + `/llms.txt` + `/.well-known/agent-commerce.json`:

```bash
curl -s http://127.0.0.1:8080/llms.txt | head -n 20
curl -s http://127.0.0.1:8080/.well-known/agent-commerce.json | python3 -m json.tool
```
