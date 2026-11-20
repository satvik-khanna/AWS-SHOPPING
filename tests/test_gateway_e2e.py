"""End-to-end tests for the 1-URL multi-protocol gateway (MCP + UCP + ACP + Discovery)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

os.environ.setdefault("SHOPPER_DATA_DIR", tempfile.mkdtemp())

from shopper.app import build_deps  # noqa: E402
from shopper.gateway import JsonStoreRegistry, make_server  # noqa: E402
from shopper.memory import JsonMemory  # noqa: E402


class GatewayE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.registry = JsonStoreRegistry(path=self.tmp / "stores.json")
        deps = build_deps()
        deps.memory = JsonMemory(path=self.tmp / "memory.json")
        self.server = make_server(host="127.0.0.1", port=0, deps=deps, registry=self.registry)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _req(self, method: str, path: str, body: dict | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        hdrs = {"Content-Type": "application/json"} if body is not None else {}
        hdrs.update(headers or {})
        req = Request(f"{self.base}{path}", data=data, headers=hdrs, method=method)
        try:
            with urlopen(req) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8")
        except HTTPError as err:
            with err:
                return err.code, dict(err.headers), err.read().decode("utf-8")

    def test_register_store_exposes_human_page_and_discovery(self):
        code, _, raw = self._req("POST", "/api/stores", {
            "name": "Shibuya Indie Merch",
            "source_url": "https://linktr.ee/shibuya-indie",
            "bio": "Limited Japanese creator drops",
            "query_filter": "Fujifilm X100V",
        })
        self.assertEqual(code, 201)
        created = json.loads(raw)
        slug = created["slug"]
        self.assertEqual(slug, "shibuya-indie-merch")

        # Human Link-in-bio page with JSON-LD + Link discovery headers
        code, hdrs, page = self._req("GET", f"/s/{slug}")
        self.assertEqual(code, 200)
        self.assertIn("application/ld+json", page)
        self.assertIn('rel="ucp-manifest"', page)
        self.assertIn('rel="mcp-server"', page)
        self.assertIn('rel="acp-checkout"', page)
        self.assertIn("ucp-manifest", hdrs.get("Link", ""))

        # AI discovery endpoints include the newly registered store
        code, _, llms = self._req("GET", "/llms.txt")
        self.assertEqual(code, 200)
        self.assertIn(f"/s/{slug}/mcp", llms)
        self.assertIn(f"/s/{slug}/.well-known/ucp", llms)

        code, _, idx_raw = self._req("GET", "/.well-known/agent-commerce.json")
        self.assertEqual(code, 200)
        idx = json.loads(idx_raw)
        self.assertTrue(any(s["slug"] == slug for s in idx["stores"]))

        code, _, sitemap = self._req("GET", "/sitemap.xml")
        self.assertEqual(code, 200)
        self.assertIn(f"/s/{slug}", sitemap)

    def test_mcp_search_cost_and_approval_gated_checkout(self):
        # initialize + tools/list
        code, _, raw = self._req("POST", "/s/tokyo-camera/mcp", {
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })
        self.assertEqual(code, 200)
        self.assertIn("protocolVersion", json.loads(raw)["result"])

        code, _, raw = self._req("POST", "/s/tokyo-camera/mcp", {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        names = [t["name"] for t in json.loads(raw)["result"]["tools"]]
        self.assertIn("search_catalog", names)
        self.assertIn("create_checkout", names)
        self.assertIn("approve_checkout", names)

        # search_catalog
        code, _, raw = self._req("POST", "/s/tokyo-camera/mcp", {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "search_catalog", "arguments": {"query": "Fujifilm X100V", "max_total_usd": 900}},
        })
        items = json.loads(raw)["result"]["structuredContent"]["items"]
        self.assertTrue(items)
        listing_id = items[0]["listing_id"]

        # create_checkout stops at awaiting_approval
        code, _, raw = self._req("POST", "/s/tokyo-camera/mcp", {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "create_checkout", "arguments": {"listing_id": listing_id}},
        })
        chk = json.loads(raw)["result"]["structuredContent"]
        self.assertEqual(chk["status"], "awaiting_approval")
        self.assertTrue(chk["approval_token"])

        # forged token fails
        code, _, raw = self._req("POST", "/s/tokyo-camera/mcp", {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "approve_checkout",
                       "arguments": {"session_id": chk["session_id"], "approval_token": "forged"}},
        })
        self.assertTrue(json.loads(raw)["result"]["isError"])

        # real single-use token succeeds once
        code, _, raw = self._req("POST", "/s/tokyo-camera/mcp", {
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "approve_checkout",
                       "arguments": {"session_id": chk["session_id"], "approval_token": chk["approval_token"]}},
        })
        done = json.loads(raw)["result"]["structuredContent"]
        self.assertEqual(done["status"], "completed")
        self.assertTrue(done["order_id"])

    def test_ucp_manifest_and_checkout_lifecycle(self):
        code, _, raw = self._req("GET", "/s/tokyo-camera/.well-known/ucp")
        self.assertEqual(code, 200)
        manifest = json.loads(raw)
        services = manifest["ucp"]["services"]["dev.ucp.shopping"]
        transports = {s["transport"] for s in services}
        self.assertEqual(transports, {"rest", "mcp"})

        code, _, raw = self._req("POST", "/s/tokyo-camera/ucp/checkout-sessions", {
            "line_items": [{"id": "mercari:m001", "quantity": 1}],
            "buyer": {"email": "buyer@example.com"},
        })
        self.assertEqual(code, 201)
        sess = json.loads(raw)
        self.assertEqual(sess["status"], "ready_for_complete")
        self.assertEqual(sess["currency"], "USD")
        self.assertTrue(sess["citations"])

        # Complete with valid approval token
        code, _, raw = self._req("POST", f"/ucp/checkout-sessions/{sess['id']}/complete", {
            "payment": {"approval_token": sess["payment"]["approval_token"]},
        })
        self.assertEqual(code, 200)
        completed = json.loads(raw)
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(completed["order"]["id"])

        # Cancel on another session
        code, _, raw = self._req("POST", "/ucp/checkout-sessions", {
            "line_items": [{"id": "mercari:m001", "quantity": 1}],
        })
        sid2 = json.loads(raw)["id"]
        code, _, raw = self._req("POST", f"/ucp/checkout-sessions/{sid2}/cancel", {})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["status"], "canceled")

    def test_acp_checkout_minor_units_idempotency_and_complete(self):
        body = {
            "line_items": [{"id": "mercari:m001", "quantity": 1}],
            "fulfillment_address": {"country": "US", "state": "CA"},
        }
        code1, _, raw1 = self._req("POST", "/s/tokyo-camera/acp/checkout_sessions", body,
                                   headers={"Idempotency-Key": "idem-001"})
        code2, _, raw2 = self._req("POST", "/s/tokyo-camera/acp/checkout_sessions", body,
                                   headers={"Idempotency-Key": "idem-001"})
        self.assertEqual(code1, 201)
        self.assertEqual(code2, 201)
        s1, s2 = json.loads(raw1), json.loads(raw2)
        self.assertEqual(s1["id"], s2["id"])
        self.assertEqual(s1["status"], "ready_for_payment")
        self.assertEqual(s1["currency"], "usd")
        self.assertIsInstance(s1["totals"]["total"], int)
        self.assertGreater(s1["totals"]["total"], 10_000)  # cents (> $100)

        # Update quantity -> totals scale
        code, _, raw_upd = self._req("POST", f"/acp/checkout_sessions/{s1['id']}", {
            "line_items": [{"id": "mercari:m001", "quantity": 2}],
        })
        self.assertEqual(code, 200)
        upd = json.loads(raw_upd)
        self.assertEqual(upd["totals"]["total"], s1["totals"]["total"] * 2)

        # Complete with delegated payment token (= approval_token)
        code, _, raw_done = self._req("POST", f"/acp/checkout_sessions/{s1['id']}/complete", {
            "payment_data": {"token": s1["approval_token"], "provider": "stripe"},
        })
        self.assertEqual(code, 200)
        done = json.loads(raw_done)
        self.assertEqual(done["status"], "completed")
        self.assertTrue(done["order"]["id"])

    def test_cors_preflight_and_input_hardening(self):
        code, hdrs, _ = self._req("OPTIONS", "/mcp")
        self.assertEqual(code, 204)
        self.assertEqual(hdrs.get("Access-Control-Allow-Origin"), "*")
        code, _, raw = self._req("POST", "/mcp", [1, 2])
        self.assertEqual(code, 400)
        self.assertIn("object", json.loads(raw)["error"])
        big = {"x": "a" * (70 * 1024)}
        code, _, _ = self._req("POST", "/mcp", big)
        self.assertEqual(code, 413)


if __name__ == "__main__":
    unittest.main()
