"""POST /api/chat + POST /api/buy over real HTTP: intents, typed components, approval gate, SIE rerank switch."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from shopper.api import make_server
from shopper.api import chat as C
from shopper.api.sie_rerank import rerank
from shopper.checkout import BuyeeCheckout, InMemorySessionStore
from shopper.cost import RulesCostEngine
from shopper.listings import FixtureSource
from shopper.memory import JsonMemory
from shopper.pipeline import Deps
from shopper.rank import PriceRanker

LIST = "list up Fujifilm X100V under $900"
CARD = {"brand": "Visa", "last4": "4242", "exp": "12/29"}


class ChatApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        mem = JsonMemory(Path(self.tmp.name) / "memory.json")
        self.store = InMemorySessionStore()
        deps = Deps(memory=mem, source=FixtureSource(), cost=RulesCostEngine(), ranker=PriceRanker(),
                    checkout=BuyeeCheckout(store=self.store, memory=mem))
        self.env = mock.patch.dict(os.environ, {"SHOPPER_LIVE": ""})
        self.env.start()
        self.srv = make_server("127.0.0.1", 0, deps=deps, quiet=True)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.env.stop()
        self.tmp.cleanup()

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                text = r.read()
                return r.status, dict(r.headers), (json.loads(text) if text else None)
        except urllib.error.HTTPError as e:
            with e:
                text = e.read()
            return e.code, dict(e.headers), (json.loads(text) if text else None)

    def chat(self, message, session=None, user="alice", **extra):
        body = {"user": user, "message": message, **extra}
        if session:
            body["session"] = session
        st, _, b = self.call("POST", "/api/chat", body)
        self.assertEqual(st, 200, b)
        return b

    @staticmethod
    def comp(resp, type_):
        found = [c for c in resp["components"] if c["type"] == type_]
        return found[0] if found else None

    # ---------------------------------------------------------------- search / list
    def test_list_up_returns_listing_table(self):
        r = self.chat(LIST)
        self.assertTrue(r["session"].startswith("chat_"))
        self.assertEqual(r["intent"], "search")
        t = self.comp(r, "listing_table")
        self.assertEqual(t["version"], C.COMPONENT_VERSION)
        self.assertEqual(t["query"], "Fujifilm X100V")
        self.assertEqual(t["budget_usd"], 900)
        self.assertEqual(t["sort"]["key"], "relevance")
        self.assertGreaterEqual(len(t["rows"]), 3)
        self.assertEqual({c["key"] for c in t["columns"] if c["sortable"]}, {"price", "condition", "relevance"})
        for row in t["rows"]:
            for k in ("id", "title", "total_usd", "condition", "source", "seller", "url", "image"):
                self.assertIn(k, row)
            self.assertLessEqual(row["total_usd"], 900)
            buy = row["actions"][0]
            self.assertEqual((buy["type"], buy["method"], buy["path"]), ("buy", "POST", "/api/buy"))
            self.assertEqual(buy["body"]["listing_id"], row["id"])
            self.assertEqual(buy["body"]["session"], r["session"])
        self.assertIn("intent: search (rule-based)", r["trail"])
        self.assertTrue(any(l.startswith("ranker: PriceRanker") and "Superlinked SIE off" in l for l in r["trail"]))
        self.assertEqual(self.store.list(), [])            # listing never mints a cart / token

    def test_sort_by_price_condition_relevance(self):
        r = self.chat(LIST)
        rel = [x["id"] for x in self.comp(r, "listing_table")["rows"]]
        p = self.comp(self.chat("sort by price", r["session"]), "listing_table")
        totals = [x["total_usd"] for x in p["rows"]]
        self.assertEqual(totals, sorted(totals))
        self.assertEqual(p["sort"], {**p["sort"], "key": "price", "dir": "asc"})
        d = self.comp(self.chat("sort by price desc", r["session"]), "listing_table")
        self.assertEqual([x["total_usd"] for x in d["rows"]], sorted(totals, reverse=True))
        c = self.comp(self.chat("sort by condition", r["session"]), "listing_table")
        ranks = [C.COND_RANK[x["condition"]] for x in c["rows"]]
        self.assertEqual(ranks, sorted(ranks, reverse=True))
        back = self.comp(self.chat("sort by relevance", r["session"]), "listing_table")
        self.assertEqual([x["id"] for x in back["rows"]], rel)
        bad = self.chat("sort by colour", r["session"])
        self.assertEqual(self.comp(bad, "notice")["level"], "warn")

    def test_sort_without_list_is_a_notice(self):
        r = self.chat("sort by price")
        self.assertIsNone(self.comp(r, "listing_table"))
        self.assertIsNotNone(self.comp(r, "notice"))

    def test_no_match_in_budget(self):
        r = self.chat("list up Fujifilm X100V under $5")
        self.assertEqual(self.comp(r, "listing_table")["rows"], [])
        self.assertEqual(self.comp(r, "notice")["level"], "warn")

    # ---------------------------------------------------------------- buy -> swipe -> approve gate
    def test_buy_row_then_swipe_approve_once(self):
        r = self.chat(LIST, payment_method=CARD)
        self.chat("sort by price", r["session"])
        b = self.chat("buy 1", r["session"])
        ab, sp, cb = self.comp(b, "agent_browser"), self.comp(b, "swipe_to_pay"), self.comp(b, "cost_breakdown")
        cheapest = min(self.comp(r, "listing_table")["rows"], key=lambda x: x["total_usd"])
        self.assertEqual(ab["status"], "cart_ready")
        self.assertEqual(ab["listing_id"], cheapest["id"])
        self.assertFalse(ab["opened_in_browser"])                 # offline: no real browser
        self.assertTrue(ab["opened_url"].startswith("https://"))
        self.assertEqual(ab["steps"][-1]["state"], "awaiting_approval")
        self.assertEqual(sp["amount_usd"], cheapest["total_usd"])
        self.assertEqual(sp["payment_method"]["label"], "Visa •••• 4242")
        self.assertGreater(sp["expires_at"], 0)
        self.assertEqual(sp["approve"]["path"], "/api/approve")
        self.assertAlmostEqual(cb["total_usd"], sum(l["amount_usd"] for l in cb["lines"]), 2)
        self.assertTrue(all(l["citation"]["source"] for l in cb["lines"]))
        token = sp["approval_token"]
        # typing "approve" in chat must not pay
        t = self.chat("approve", r["session"])
        self.assertIsNone(self.comp(t, "swipe_to_pay"))
        self.assertEqual(self.store.list()[0].state, "awaiting_approval")
        st, _, c = self.call("POST", "/api/approve", sp["approve"]["body"])
        self.assertEqual((st, c["status"]), (200, "placed"))
        st, _, again = self.call("POST", "/api/approve", {"approval_token": token})
        self.assertEqual(again["status"], "blocked")
        st, _, forged = self.call("POST", "/api/approve", {"approval_token": token[:-1] + "x"})
        self.assertEqual(forged["status"], "blocked")
        placed = [s for s in self.store.list() if s.state == "placed"]
        self.assertEqual(len(placed), 1)

    def test_buy_by_id_and_unknown(self):
        r = self.chat(LIST)
        rid = self.comp(r, "listing_table")["rows"][2]["id"]
        b = self.chat(f"buy {rid.split(':', 1)[1]}", r["session"])
        self.assertEqual(self.comp(b, "swipe_to_pay")["listing_id"], rid)
        u = self.chat("buy zz999", r["session"])
        self.assertIsNone(self.comp(u, "swipe_to_pay"))
        self.assertEqual(self.comp(u, "notice")["level"], "warn")

    def test_new_cart_or_search_revokes_previous_token(self):
        r = self.chat(LIST)
        t1 = self.comp(self.chat("buy 1", r["session"]), "swipe_to_pay")["approval_token"]
        t2 = self.comp(self.chat("buy 2", r["session"]), "swipe_to_pay")["approval_token"]
        self.assertEqual(self.call("POST", "/api/approve", {"approval_token": t1})[2]["status"], "blocked")
        self.chat(LIST, r["session"])                                 # new search supersedes t2
        self.assertEqual(self.call("POST", "/api/approve", {"approval_token": t2})[2]["status"], "blocked")
        self.assertEqual([s for s in self.store.list() if s.state == "placed"], [])

    def test_chat_reject_cancels_cart(self):
        r = self.chat(LIST)
        tok = self.comp(self.chat("buy 1", r["session"]), "swipe_to_pay")["approval_token"]
        self.chat("cancel", r["session"])
        self.assertEqual(self.call("POST", "/api/approve", {"approval_token": tok})[2]["status"], "blocked")

    # ---------------------------------------------------------------- /api/buy (web/server.py contract)
    def test_buy_endpoint_web_contract(self):
        body = {"query": "Fujifilm X100V", "budget": 900, "ship_to": "US-CA", "user_id": "alice",
                "condition_min": None, "listing_id": "mercari:m005", "open_real": True}
        st, _, out = self.call("POST", "/api/buy", body)
        self.assertEqual(st, 200, out)
        self.assertEqual(out["cart"]["status"], "awaiting_approval")
        self.assertEqual(out["cart"]["listing_id"], "mercari:m005")
        self.assertFalse(out["cua"]["opened_in_chrome"])               # open_real ignored offline
        self.assertIn("picks", out)
        self.assertIn("total_usd", out["picks"][0]["cost"])
        self.assertEqual({c["type"] for c in out["components"]}, {"agent_browser", "swipe_to_pay", "cost_breakdown"})
        self.assertEqual(len(self.store.list()), 1)                    # only the chosen item got a cart
        st, _, c = self.call("POST", "/api/approve", {"approval_token": out["cart"]["approval_token"]})
        self.assertEqual(c["status"], "placed")

    def test_buy_endpoint_errors(self):
        st, _, b = self.call("POST", "/api/buy", {"query": "Fujifilm X100V", "budget": 900})
        self.assertEqual((st, b["error"]["code"]), (400, "bad_request"))
        st, _, b = self.call("POST", "/api/buy", {"query": "Fujifilm X100V", "budget": 900, "listing_id": "mercari:m003"})
        self.assertEqual((st, b["error"]["code"]), (404, "not_found"))   # over budget -> not offered

    def test_buy_endpoint_with_chat_session_uses_table_action(self):
        r = self.chat(LIST)
        row = self.comp(r, "listing_table")["rows"][1]
        st, _, out = self.call("POST", row["actions"][0]["path"], row["actions"][0]["body"])
        self.assertEqual(st, 200, out)
        self.assertEqual(out["cart"]["listing_id"], row["id"])
        self.assertIn(f"buy {row['id']} from chat session {r['session']}", out["trail"])

    # ---------------------------------------------------------------- input safety + sessions
    def test_payment_method_never_takes_card_numbers(self):
        for pm in ({"brand": "Visa", "last4": "4242424242424242", "exp": "12/29"},
                   {**CARD, "cvv": "123"}, {**CARD, "number": "4242424242424242"},
                   {**CARD, "provider_token": "4242424242424242"}, {**CARD, "exp": "2029-12"}, "4242"):
            st, _, b = self.call("POST", "/api/chat", {"user": "alice", "message": LIST, "payment_method": pm})
            self.assertEqual(st, 400, pm)
        r = self.chat(LIST, payment_method={**CARD, "provider_token": "tok_abc123"})
        self.assertNotIn("tok_abc123", json.dumps(r))

    def test_sessions_are_per_user(self):
        r = self.chat(LIST)
        st, _, b = self.call("POST", "/api/chat", {"user": "mallory", "message": "buy 1", "session": r["session"]})
        self.assertEqual((st, b["error"]["code"]), (403, "forbidden"))
        n = self.chat("help", session="chat_doesnotexist")
        self.assertNotEqual(n["session"], "chat_doesnotexist")

    def test_message_validation(self):
        for body in ({}, {"message": 5}, {"message": "x" * 501}):
            self.assertEqual(self.call("POST", "/api/chat", body)[0], 400, body)
        h = self.chat("")
        self.assertEqual(h["intent"], "help")
        self.assertIsNotNone(self.comp(h, "notice"))

    def test_cors_preflight_chat(self):
        st, h, _ = self.call("OPTIONS", "/api/chat")
        self.assertEqual(st, 204)
        self.assertEqual(h["Access-Control-Allow-Origin"], "*")


class IntentAndRerankTest(unittest.TestCase):
    def test_parse_search(self):
        self.assertEqual(C.parse_search("list up Fujifilm X100V under $900 shipped to US-NY"),
                         {"ship_to": "US-NY", "budget": 900.0, "query": "Fujifilm X100V"})
        self.assertEqual(C.parse_search("find me a used Ricoh GR III below 650")["budget"], 650.0)
        self.assertEqual(C.parse_intent("buy #2"), ("buy", {"target": "2"}))
        self.assertEqual(C.parse_intent("sort by condition")[1], {"key": "condition", "dir": "desc"})
        self.assertEqual(C.parse_intent("cheapest first")[1], {"key": "price", "dir": "asc"})
        self.assertEqual(C.parse_intent("order by price")[0], "sort")

    def _picks(self):
        from shopper.models import Citation, CostLine, LandedCost, Listing, RankedPick
        out = []
        for i in range(3):
            l = Listing(id=f"mercari:t{i}", source="mercari", url="https://x", title_ja="t", title_en=f"t{i}",
                        price_jpy=1000, condition="good", seller="s", ships_abroad=False)
            out.append(RankedPick(l, LandedCost(l.id, [CostLine("item", 10.0, Citation("c", "2026-11-20"))], 150.0),
                                  1.0 - i / 10, "r"))
        return out

    def test_sie_rerank_reorders_and_names_engine(self):
        picks = self._picks()
        out, line = rerank("q", picks, "PriceRanker",
                           scorer=lambda q, docs: ({d[0]: i for i, d in enumerate(docs)}, "sie:test-model"))
        self.assertEqual([p.listing.id for p in out], ["mercari:t2", "mercari:t1", "mercari:t0"])
        self.assertEqual(line, "ranker: Superlinked SIE rerank (sie:test-model) over PriceRanker")

    def test_sie_failure_keeps_pipeline_order(self):
        picks = self._picks()

        def boom(q, docs):
            raise ConnectionError("down")
        out, line = rerank("q", picks, "PriceRanker", scorer=boom)
        self.assertEqual(out, picks)
        self.assertIn("Superlinked SIE failed (ConnectionError", line)

    def test_sie_needs_live_and_key(self):
        picks = self._picks()
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "", "SIE_API_KEY": "k"}):
            self.assertIn("off (offline)", rerank("q", picks, "R")[1])
        env = {k: v for k, v in os.environ.items() if k not in ("SIE_API_KEY", "SIE_BASE_URL")}
        env["SHOPPER_LIVE"] = "1"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIn("skipped (SIE_API_KEY not set)", rerank("q", picks, "R")[1])


if __name__ == "__main__":
    unittest.main()
