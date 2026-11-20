"""End-to-end on the offline base wiring."""
import os
import tempfile
import unittest

os.environ.setdefault("SHOPPER_DATA_DIR", tempfile.mkdtemp())

from shopper.app import build_deps  # noqa: E402
from shopper.models import ShoppingRequest  # noqa: E402
from shopper.pipeline import run  # noqa: E402


class BaseE2E(unittest.TestCase):
    def setUp(self):
        self.deps = build_deps()
        self.req = ShoppingRequest(query="Fujifilm X100V", max_total_usd=900, ship_to="US-CA")

    def test_flow_stops_for_approval(self):
        r = run(self.req, self.deps)
        self.assertTrue(r.picks)
        self.assertEqual(r.cart.status, "awaiting_approval")
        for p in r.picks:
            self.assertLessEqual(p.cost.total_usd, 900)
            for line in p.cost.lines:
                self.assertTrue(line.citation.source and line.citation.as_of)
        self.assertTrue(any("banned" in t for t in r.trail))   # loose battery blocked
        self.assertTrue(any("budget" in t for t in r.trail))   # new one over budget

    def test_approval_token_single_use(self):
        r = run(self.req, self.deps)
        tok = r.cart.approval_token
        self.assertEqual(self.deps.checkout.approve(tok).status, "placed")
        self.assertEqual(self.deps.checkout.approve(tok).status, "blocked")
        self.assertEqual(self.deps.checkout.approve("forged").status, "blocked")

    def test_rejected_seller_excluded(self):
        self.deps.memory.reject_seller("default", "camera_taro")
        r = run(self.req, self.deps)
        self.assertNotIn("camera_taro", [p.listing.seller for p in r.picks])


if __name__ == "__main__":
    unittest.main()
