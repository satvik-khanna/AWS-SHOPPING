import json
import os
import unittest
from unittest import mock

from shopper.cost import RulesCostEngine
from shopper.listings import FixtureSource
from shopper.models import Preferences, ShoppingRequest
from shopper.rank import ClaudeRerank, SmartRanker, default_ranker


def run(query, budget=900.0, prefs=None, ranker=None, **kw):
    r = ShoppingRequest(query, budget, "US-CA", **kw)
    p = prefs or Preferences("t")
    cost = RulesCostEngine()
    cands = []
    for l in FixtureSource().search(r, p):
        c = cost.landed_cost(l, r)
        if not c.blocked_reason and c.total_usd <= budget:
            cands.append((l, c))
    return (ranker or SmartRanker()).rank(r, p, cands)


class SmartRankerTest(unittest.TestCase):
    def test_real_camera_beats_cheap_box_and_junk(self):
        picks = run("Fujifilm X100V")
        top = picks[0].listing
        self.assertNotEqual(top.fields.get("item_type"), "accessory")
        self.assertNotEqual(top.condition, "junk")
        bottom_ids = [p.listing.id for p in picks[-3:]]
        self.assertTrue(any(p.listing.fields.get("item_type") == "accessory" for p in picks[-4:]), bottom_ids)

    def test_reason_is_human_readable(self):
        p = run("Fujifilm X100V")[0]
        self.assertIn("landed", p.reason)
        self.assertIn("under budget", p.reason)
        self.assertIn("condition", p.reason)
        box = next(x for x in run("Fujifilm X100V") if x.listing.fields.get("item_type") == "accessory")
        self.assertIn("Watch out", box.reason)

    def test_condition_floor_drops(self):
        picks = run("Fujifilm X100V", condition_min="like_new")
        self.assertTrue(picks)
        self.assertTrue(all(p.listing.condition in ("like_new", "new") for p in picks))

    def test_shoe_size_preference(self):
        prefs = Preferences("t", sizes={"shoe_cm": "27"})
        picks = run("Nike Dunk Low", budget=500, prefs=prefs)
        self.assertEqual(picks[0].listing.fields.get("size_cm"), "27")
        self.assertIn("your size", picks[0].reason)
        wrong = [p for p in picks if p.listing.fields.get("size_cm") not in (None, "27")]
        self.assertTrue(all("you wear 27" in p.reason for p in wrong))

    def test_rejected_seller_and_past_purchase(self):
        base = run("Fujifilm X100V")
        top = base[0].listing
        picks = run("Fujifilm X100V", prefs=Preferences("t", rejected_sellers=[top.seller]))
        self.assertNotIn(top.seller, {p.listing.seller for p in picks})
        picks = run("Fujifilm X100V", prefs=Preferences("t", past_purchases=[top.id]))
        mine = next(p for p in picks if p.listing.id == top.id)
        self.assertIn("already bought", mine.reason)
        self.assertNotEqual(picks[0].listing.id, top.id)

    def test_scores_sorted(self):
        s = [p.score for p in run("Fujifilm X100V")]
        self.assertEqual(s, sorted(s, reverse=True))


class ClaudeRerankTest(unittest.TestCase):
    def test_offline_never_calls(self):
        def boom(body, key):
            raise AssertionError("called API offline")
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "", "ANTHROPIC_API_KEY": "x"}):
            picks = run("Fujifilm X100V", ranker=ClaudeRerank(SmartRanker(), post=boom))
        self.assertTrue(picks)

    def test_live_reorders_and_falls_back(self):
        offline = run("Fujifilm X100V")
        ids = [p.listing.id for p in offline][:8]
        answer = {"order": [{"id": i, "reason": "chosen by test"} for i in reversed(ids)]}
        post = lambda body, key: {"content": [{"type": "text", "text": json.dumps(answer)}]}
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "1", "ANTHROPIC_API_KEY": "x"}):
            picks = run("Fujifilm X100V", ranker=ClaudeRerank(SmartRanker(), post=post))
            self.assertEqual(picks[0].listing.id, ids[-1])
            self.assertIn("[Claude]", picks[0].reason)

            def bad(body, key):
                raise OSError("network down")
            picks = run("Fujifilm X100V", ranker=ClaudeRerank(SmartRanker(), post=bad))
        self.assertEqual([p.listing.id for p in picks], [p.listing.id for p in offline])

    def test_default_ranker_offline_matches_smart(self):
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": ""}):
            self.assertEqual([p.listing.id for p in run("Fujifilm X100V", ranker=default_ranker())],
                             [p.listing.id for p in run("Fujifilm X100V")])


if __name__ == "__main__":
    unittest.main()
