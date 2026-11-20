"""Demo screen server (web/): page, JSON API shape, approval gate, reject seller, input hardening."""
import json
import os
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("SHOPPER_DATA_DIR", tempfile.mkdtemp())

from shopper.app import build_deps  # noqa: E402
from shopper.memory import JsonMemory  # noqa: E402
from shopper.models import ShoppingRequest  # noqa: E402
from shopper.pipeline import run  # noqa: E402
from web.server import STATIC, create_server  # noqa: E402

REQ = {"query": "Fujifilm X100V", "budget": 900, "ship_to": "US-CA"}


def fresh_deps():
    """Offline wiring with its own memory file: other test modules share SHOPPER_DATA_DIR in this process."""
    deps = build_deps()
    deps.memory = JsonMemory(Path(tempfile.mkdtemp()) / "memory.json")
    return deps


class WebCase(unittest.TestCase):
    def setUp(self):
        self.deps = fresh_deps()
        self.httpd = create_server(port=0, deps=self.deps, quiet=True)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, path, body=None, method=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"Content-Type": "application/json"} if body is not None else {}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.headers, e.read()

    def post(self, path, body):
        status, _, data = self.call(path, body)
        return status, json.loads(data)


class Page(WebCase):
    def test_page_and_assets_served(self):
        status, headers, body = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        html = body.decode("utf-8")
        for anchor in ('id="form"', 'id="prefs"', 'id="picks"', 'id="gate"', 'id="trail"'):
            self.assertIn(anchor, html)
        for asset in re.findall(r'(?:src|href)="([^"]+)"', html):
            self.assertEqual(self.call(asset)[0], 200, asset)

    def test_no_external_assets(self):
        """No CDN, no build step: the page only loads its own files and the CSP says so."""
        _, headers, _ = self.call("/")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertEqual([u for u in re.findall(r'(?:src|href)="([^"]+)"', html) if not u.startswith("/static/")], [])
        self.assertNotIn("<style", html)
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
        css = (STATIC / "app.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"@import|url\(")

    def test_unknown_paths_and_traversal(self):
        for path in ("/nope", "/static/nope.js", "/static/../server.py", "/static/%2e%2e/server.py", "/server.py"):
            self.assertEqual(self.call(path)[0], 404, path)


class Api(WebCase):
    def test_run_matches_cli_json_shape(self):
        status, out = self.post("/api/run", REQ)
        self.assertEqual(status, 200)
        cli = asdict(run(ShoppingRequest("Fujifilm X100V", 900, "US-CA"), fresh_deps()))
        self.assertEqual(set(out) - {"meta"}, set(cli))
        self.assertEqual(out["request"], cli["request"])
        self.assertEqual([p["listing"] for p in out["picks"]], [p["listing"] for p in cli["picks"]])
        self.assertTrue(out["picks"])
        for pick in out["picks"]:
            lines = pick["cost"]["lines"]
            self.assertAlmostEqual(pick["cost"]["total_usd"], sum(l["amount_usd"] for l in lines), places=2)
            for line in lines:      # the UI prints source + as_of on every line
                self.assertTrue(line["citation"]["source"] and line["citation"]["as_of"])
        self.assertEqual(out["cart"]["status"], "awaiting_approval")
        self.assertEqual(out["cart"]["listing_id"], out["picks"][0]["listing"]["id"])
        self.assertTrue(out["cart"]["approval_token"])
        self.assertFalse(out["meta"]["live"])
        self.assertEqual(self.call("/api/meta")[0], 200)

    def test_nothing_placed_until_approve_and_token_is_single_use(self):
        _, out = self.post("/api/run", REQ)
        token = out["cart"]["approval_token"]
        self.assertEqual(self.deps.memory.recall("default").past_purchases, [])    # run alone buys nothing
        self.assertEqual(self.post("/api/approve", {"token": "forged"})[1]["cart"]["status"], "blocked")
        self.assertEqual(self.deps.memory.recall("default").past_purchases, [])
        status, ok = self.post("/api/approve", {"token": token})
        self.assertEqual((status, ok["cart"]["status"]), (200, "placed"))
        self.assertEqual(ok["cart"]["listing_id"], out["cart"]["listing_id"])
        self.assertEqual(self.post("/api/approve", {"token": token})[1]["cart"]["status"], "blocked")
        # the purchase is remembered once and recalled on the next run
        _, again = self.post("/api/run", REQ)
        self.assertEqual(again["preferences"]["past_purchases"], [out["cart"]["listing_id"]])

    def test_reject_seller_reruns_without_them_and_kills_old_token(self):
        _, first = self.post("/api/run", REQ)
        seller = first["picks"][0]["listing"]["seller"]
        old_token = first["cart"]["approval_token"]
        status, out = self.post("/api/reject", dict(REQ, seller=seller))
        self.assertEqual(status, 200)
        self.assertIn(seller, out["preferences"]["rejected_sellers"])
        self.assertNotIn(seller, [p["listing"]["seller"] for p in out["picks"]])
        self.assertIn(seller, out["trail"][0])
        self.assertNotEqual(out["cart"]["approval_token"], old_token)
        # the cart from the rejected seller can no longer be approved from this screen
        self.assertEqual(self.post("/api/approve", {"token": old_token})[1]["cart"]["status"], "blocked")
        self.assertEqual(self.deps.memory.recall("default").past_purchases, [])
        self.assertEqual(self.post("/api/approve", {"token": out["cart"]["approval_token"]})[1]["cart"]["status"], "placed")

    def test_new_search_supersedes_previous_cart(self):
        _, a = self.post("/api/run", REQ)
        _, b = self.post("/api/run", REQ)
        self.assertEqual(self.post("/api/approve", {"token": a["cart"]["approval_token"]})[1]["cart"]["status"], "blocked")
        self.assertEqual(self.post("/api/approve", {"token": b["cart"]["approval_token"]})[1]["cart"]["status"], "placed")

    def test_no_candidate_means_no_cart(self):
        status, out = self.post("/api/run", dict(REQ, budget=1))
        self.assertEqual(status, 200)
        self.assertEqual((out["picks"], out["cart"]), ([], None))

    def test_condition_min_is_passed_through(self):
        _, out = self.post("/api/run", dict(REQ, condition_min="like_new"))
        self.assertEqual(out["request"]["condition_min"], "like_new")
        self.assertEqual({p["listing"]["condition"] for p in out["picks"]} - {"like_new", "new"}, set())

    def test_buy_second_pick_prepares_cart_and_approves(self):
        _, first = self.post("/api/run", REQ)
        self.assertGreaterEqual(len(first["picks"]), 2)
        second_id = first["picks"][1]["listing"]["id"]
        old_token = first["cart"]["approval_token"]
        status, bought = self.post("/api/buy", dict(REQ, listing_id=second_id))
        self.assertEqual(status, 200)
        self.assertEqual(bought["cart"]["listing_id"], second_id)
        self.assertEqual(bought["cart"]["status"], "awaiting_approval")
        self.assertEqual(self.post("/api/approve", {"token": old_token})[1]["cart"]["status"], "blocked")
        _, placed = self.post("/api/approve", {"token": bought["cart"]["approval_token"]})
        self.assertEqual(placed["cart"]["status"], "placed")
        self.assertEqual(placed["cart"]["listing_id"], second_id)


class Hardening(WebCase):
    def test_bad_input_is_400_with_a_message(self):
        for body in ({}, {"query": " ", "budget": 900}, {"query": "x"}, {"query": "x", "budget": "abc"},
                     {"query": "x", "budget": 0}, {"query": "x", "budget": float("inf")},
                     {"query": "x", "budget": 900, "condition_min": "mint"}, {"query": "x" * 301, "budget": 900}):
            status, out = self.post("/api/run", body)
            self.assertEqual(status, 400, body)
            self.assertTrue(out["error"])
        self.assertEqual(self.post("/api/approve", {})[0], 400)
        self.assertEqual(self.post("/api/reject", REQ)[0], 400)
        self.assertEqual(self.call("/api/run", raw=b"{not json", headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.call("/api/run", raw=b"[1]", headers={"Content-Type": "application/json"})[0], 400)

    def test_state_changes_need_a_json_post(self):
        """A plain link or cross-site form cannot run a search, approve or reject."""
        for path in ("/api/run", "/api/approve", "/api/reject"):
            self.assertEqual(self.call(path)[0], 405, path)
            form = self.call(path, raw=b"token=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
            self.assertEqual(form[0], 415, path)

    def test_foreign_host_header_refused(self):
        self.assertEqual(self.call("/", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("/api/run", REQ, headers={"Host": "evil.example:80"})[0], 403)
        self.assertEqual(self.call("/", headers={"Host": "localhost:1234"})[0], 200)

    def test_module_failure_is_reported_not_hung(self):
        class Boom:
            def search(self, req, prefs):
                raise RuntimeError("marketplace down")
        self.deps.source = Boom()
        status, out = self.post("/api/run", REQ)
        self.assertEqual(status, 500)
        self.assertIn("marketplace down", out["error"])


if __name__ == "__main__":
    unittest.main()
