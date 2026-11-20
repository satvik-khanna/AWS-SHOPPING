"""Caller-shape test: the pipeline with in-test fakes. Each module's real impl must satisfy the same shape."""
import unittest

from shopper.models import (CartResult, Citation, CostLine, LandedCost, Listing,
                            Preferences, RankedPick, ShoppingRequest)
from shopper.pipeline import Deps, run

CIT = Citation(source="test", as_of="2026-11-20")


def listing(i, seller="s", price=50000):
    return Listing(id=f"mercari:{i}", source="mercari", url=f"https://example.invalid/{i}",
                   title_ja="富士フイルム X100V", title_en="Fujifilm X100V", price_jpy=price,
                   condition="good", seller=seller, ships_abroad=False, category="camera")


class FakeMemory:
    def recall(self, user_id): return Preferences(user_id=user_id, rejected_sellers=["bad"])
    def remember_purchase(self, user_id, listing): pass
    def reject_seller(self, user_id, seller): pass


class FakeSource:
    def search(self, req, prefs): return [listing(1), listing(2, seller="bad"), listing(3, price=500000)]


class FakeCost:
    def landed_cost(self, l, req):
        return LandedCost(listing_id=l.id, fx_jpy_per_usd=150.0,
                          lines=[CostLine("item price", round(l.price_jpy / 150, 2), CIT)])


class FakeRanker:
    def rank(self, req, prefs, candidates):
        return [RankedPick(l, c, 1.0, "cheapest") for l, c in candidates]


class FakeCheckout:
    def fill_cart(self, pick):
        return CartResult(status="awaiting_approval", listing_id=pick.listing.id, approval_token="tok")
    def approve(self, token): raise AssertionError("pipeline must never approve")


class PipelineContract(unittest.TestCase):
    def test_stops_at_approval_and_filters(self):
        req = ShoppingRequest(query="used Fujifilm X100V", max_total_usd=900, ship_to="US-CA")
        r = run(req, Deps(FakeMemory(), FakeSource(), FakeCost(), FakeRanker(), FakeCheckout()))
        self.assertEqual([p.listing.id for p in r.picks], ["mercari:1"])   # bad seller + over budget dropped
        self.assertEqual(r.cart.status, "awaiting_approval")
        self.assertTrue(any("budget" in t for t in r.trail))


if __name__ == "__main__":
    unittest.main()
