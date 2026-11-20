"""shopper.api over real HTTP on a random port: every endpoint, CORS, errors, approval gate."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from shopper.api import make_server
from shopper.checkout import DryRunCheckout
from shopper.cost import RulesCostEngine
from shopper.listings import FixtureSource
from shopper.memory import JsonMemory
from shopper.pipeline import Deps
from shopper.rank import PriceRanker

RUN = {"query": "Fujifilm X100V", "budget": 900, "ship_to": "US-CA", "user": "alice"}


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        deps = Deps(memory=JsonMemory(Path(self.tmp.name) / "memory.json"), source=FixtureSource(),
                    cost=RulesCostEngine(), ranker=PriceRanker(), checkout=DryRunCheckout())
        self.srv = make_server("127.0.0.1", 0, deps=deps, quiet=True)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, raw=None, headers=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                text = r.read()
                return r.status, dict(r.headers), (json.loads(text) if text else None)
        except urllib.error.HTTPError as e:
            with e:
                text = e.read()
            return e.code, dict(e.headers), (json.loads(text) if text else None)

    # --- endpoints -------------------------------------------------------
    def test_health(self):
        st, h, b = self.call("GET", "/health")
        self.assertEqual(st, 200)
        self.assertTrue(b["ok"])
        self.assertEqual(b["wiring"]["checkout"], "DryRunCheckout")
        self.assertEqual(h["Access-Control-Allow-Origin"], "*")

    def test_run_full_result(self):
        st, _, b = self.call("POST", "/api/run", RUN)
        self.assertEqual(st, 200)
        self.assertEqual(set(b), {"request", "preferences", "picks", "cart", "trail"})
        self.assertEqual(b["request"]["user_id"], "alice")
        self.assertEqual(b["preferences"]["user_id"], "alice")
        self.assertTrue(b["picks"])
        for p in b["picks"]:
            self.assertLessEqual(p["cost"]["total_usd"], 900)
            self.assertAlmostEqual(p["cost"]["total_usd"], sum(l["amount_usd"] for l in p["cost"]["lines"]), 2)
            for line in p["cost"]["lines"]:
                self.assertTrue(line["citation"]["source"])
        self.assertEqual(b["cart"]["status"], "awaiting_approval")
        self.assertTrue(b["cart"]["approval_token"])
        self.assertEqual(b["cart"]["listing_id"], b["picks"][0]["listing"]["id"])
        self.assertTrue(b["trail"])

    def test_run_nothing_in_budget(self):
        st, _, b = self.call("POST", "/api/run", {**RUN, "budget": 1})
        self.assertEqual(st, 200)
        self.assertEqual(b["picks"], [])
        self.assertIsNone(b["cart"])

    def test_reject_and_memory(self):
        _, _, run1 = self.call("POST", "/api/run", RUN)
        seller = run1["picks"][0]["listing"]["seller"]
        st, _, prefs = self.call("POST", "/api/reject", {"user": "alice", "seller": seller})
        self.assertEqual(st, 200)
        self.assertEqual(prefs["rejected_sellers"], [seller])
        st, _, again = self.call("POST", "/api/reject", {"user": "alice", "seller": seller})
        self.assertEqual(again["rejected_sellers"], [seller])          # idempotent
        st, _, mem = self.call("GET", "/api/memory?user=alice")
        self.assertEqual(st, 200)
        self.assertEqual(mem["rejected_sellers"], [seller])
        _, _, other = self.call("GET", "/api/memory?user=bob")
        self.assertEqual(other["rejected_sellers"], [])                 # per user
        _, _, run2 = self.call("POST", "/api/run", RUN)
        self.assertNotIn(seller, [p["listing"]["seller"] for p in run2["picks"]])

    # --- approval gate ---------------------------------------------------
    def test_approve_once_then_reused_blocked(self):
        _, _, run1 = self.call("POST", "/api/run", RUN)
        token = run1["cart"]["approval_token"]
        st, _, cart = self.call("POST", "/api/approve", {"approval_token": token})
        self.assertEqual(st, 200)
        self.assertEqual(cart["status"], "placed")
        self.assertEqual(cart["listing_id"], run1["cart"]["listing_id"])
        _, _, mem = self.call("GET", "/api/memory?user=alice")
        self.assertEqual(mem["past_purchases"], [run1["cart"]["listing_id"]])
        st, _, again = self.call("POST", "/api/approve", {"approval_token": token})
        self.assertEqual(st, 200)
        self.assertEqual(again["status"], "blocked")
        self.assertNotEqual(again["status"], "placed")
        _, _, mem = self.call("GET", "/api/memory?user=alice")
        self.assertEqual(len(mem["past_purchases"]), 1)                 # no second order

    def test_forged_token_blocked(self):
        self.call("POST", "/api/run", RUN)
        for forged in ("forged-token", "x" * 11, "' OR 1=1 --"):
            st, _, cart = self.call("POST", "/api/approve", {"approval_token": forged})
            self.assertEqual(st, 200)
            self.assertEqual(cart["status"], "blocked", forged)
            self.assertIsNone(cart["approval_token"])
        _, _, mem = self.call("GET", "/api/memory?user=alice")
        self.assertEqual(mem["past_purchases"], [])

    def test_token_survives_between_requests(self):
        _, _, a = self.call("POST", "/api/run", RUN)
        _, _, b = self.call("POST", "/api/run", {**RUN, "user": "bob"})
        self.call("GET", "/health")
        self.assertEqual(self.call("POST", "/api/approve", {"approval_token": a["cart"]["approval_token"]})[2]["status"], "placed")
        self.assertEqual(self.call("POST", "/api/approve", {"approval_token": b["cart"]["approval_token"]})[2]["status"], "placed")

    def test_approve_missing_token_400(self):
        for body in ({}, {"approval_token": ""}, {"approval_token": 123}, {"token": "abc"}):
            st, _, b = self.call("POST", "/api/approve", body)
            self.assertEqual(st, 400, body)
            self.assertEqual(b["error"]["code"], "bad_request")

    # --- errors + CORS ---------------------------------------------------
    def test_run_validation_400(self):
        bad = [{}, {"budget": 900}, {"query": "  ", "budget": 900}, {"query": "x"},
               {"query": "x", "budget": "abc"}, {"query": "x", "budget": -1}, {"query": "x", "budget": True},
               {"query": "x", "budget": "nan"}, {"query": "x" * 301, "budget": 9},
               {"query": "x", "budget": 9, "condition_min": "mint"}, {"query": "x", "budget": 9, "user": 5}]
        for body in bad:
            st, h, b = self.call("POST", "/api/run", body)
            self.assertEqual(st, 400, body)
            self.assertIn("message", b["error"])
            self.assertEqual(h["Access-Control-Allow-Origin"], "*")

    def test_bad_json_and_body(self):
        self.assertEqual(self.call("POST", "/api/run", raw=b"{not json")[0], 400)
        self.assertEqual(self.call("POST", "/api/run", raw=b"[1,2]")[0], 400)
        self.assertEqual(self.call("POST", "/api/run", raw=b"")[0], 400)
        st, _, b = self.call("POST", "/api/run", raw=b"x" * (64 * 1024 + 1))
        self.assertEqual(st, 413)
        self.assertEqual(b["error"]["code"], "too_large")

    def test_reject_requires_seller(self):
        st, _, b = self.call("POST", "/api/reject", {"user": "alice"})
        self.assertEqual(st, 400)

    def test_404_and_405(self):
        st, _, b = self.call("GET", "/api/nope")
        self.assertEqual((st, b["error"]["code"]), (404, "not_found"))
        st, _, b = self.call("GET", "/api/run")
        self.assertEqual((st, b["error"]["code"]), (405, "method_not_allowed"))
        st, _, b = self.call("POST", "/health", {})
        self.assertEqual(st, 405)

    def test_cors_preflight(self):
        st, h, _ = self.call("OPTIONS", "/api/run", headers={"Origin": "http://localhost:5173",
                                                              "Access-Control-Request-Method": "POST"})
        self.assertEqual(st, 204)
        self.assertEqual(h["Access-Control-Allow-Origin"], "*")
        self.assertIn("POST", h["Access-Control-Allow-Methods"])
        self.assertIn("Content-Type", h["Access-Control-Allow-Headers"])


if __name__ == "__main__":
    unittest.main()
