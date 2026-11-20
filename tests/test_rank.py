import json
import os
import unittest
from unittest import mock

from shopper.cost import RulesCostEngine
from shopper.listings import FixtureSource
from shopper.models import Preferences, ShoppingRequest
from shopper.rank import ClaudeRerank, SIERerank, SmartRanker, default_ranker


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
        self.assertNotEqual(top.condition, "junk")
        self.assertFalse(any(p.listing.fields.get("item_type") == "accessory" for p in picks))  # box / case dropped
        self.assertEqual(picks[-1].listing.condition, "junk")

    def test_accessory_kept_when_asked(self):
        picks = run("Fujifilm X100V leather case", budget=300)
        self.assertTrue(any(p.listing.fields.get("item_type") == "accessory" for p in picks))

    def test_reason_is_human_readable(self):
        picks = run("Fujifilm X100V")
        p = picks[0]
        self.assertIn("landed", p.reason)
        self.assertIn("under budget", p.reason)
        self.assertIn("condition", p.reason)
        self.assertIn("Watch out", picks[-1].reason)

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


class DemoQueryTest(unittest.TestCase):
    """The 3 niche demo scenarios from SURVEY / docs/survey-genres-and-rules.md."""

    def test_indie_goods(self):
        picks = run("limited-edition risograph artbook or acrylic stand from Japanese indie artist on BOOTH/BASE "
                    "under $80, shipped to US-CA", budget=80)
        self.assertGreaterEqual(len(picks), 5)
        for p in picks[:3]:
            self.assertIn(p.listing.fields.get("marketplace"), ("BOOTH", "BASE"))
            self.assertIn("proxy buys and forwards", p.reason)

    def test_vintage_sweatshirt_size_l(self):
        picks = run("Kapital or vintage Champion Reverse Weave sweatshirt, size L, under $180, shipped to US-CA",
                    budget=180)
        self.assertEqual(picks[0].listing.id, "mercari:m043")         # vintage, size L, good, no flaws
        self.assertIn("your size", picks[0].reason)
        top3 = [p.listing.fields.get("size_letter") for p in picks[:3]]
        self.assertEqual(top3, ["L", "L", "L"])
        self.assertNotIn("mercari:m044", [p.listing.id for p in picks])   # over budget after duty

    def test_size_from_memory(self):
        picks = run("vintage Champion Reverse Weave sweatshirt", budget=180,
                    prefs=Preferences("t", sizes={"top": "L"}))
        self.assertEqual(picks[0].listing.fields.get("size_letter"), "L")
        self.assertTrue(any("you wear L" in p.reason for p in picks))

    def test_hard_off_junk(self):
        picks = run("junk Sony Walkman or Game Boy from Hard Off Net Mall under $60, shipped to US-CA", budget=60)
        self.assertGreaterEqual(len(picks), 4)
        top = picks[0].listing
        self.assertEqual(top.fields.get("junk_risk"), "repairable")
        self.assertIn("repair bet", picks[0].reason)
        self.assertIn("no returns", picks[0].reason)
        parts = [i for i, p in enumerate(picks) if p.listing.fields.get("junk_risk") == "parts only"]
        repair = [i for i, p in enumerate(picks) if p.listing.fields.get("junk_risk") == "repairable"]
        self.assertLess(max(repair), min(parts))


class SIERerankTest(unittest.TestCase):
    def test_offline_never_calls(self):
        async def boom(q, docs):
            raise AssertionError("called SIE offline")
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "", "SIE_API_KEY": "x"}):
            self.assertTrue(run("Fujifilm X100V", ranker=SIERerank(SmartRanker(), score=boom)))

    def test_live_blends_scores_and_falls_back(self):
        offline = run("Fujifilm X100V")
        last = offline[-1].listing.id

        async def favour_last(q, docs):
            return {i: (1.0 if i == last else 0.0) for i, _ in docs}, "sie:stub"
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "1", "SIE_API_KEY": "x"}):
            r = SIERerank(SmartRanker(), score=favour_last)
            picks = run("Fujifilm X100V", ranker=r)
            self.assertIn("Superlinked SIE relevance 1.00", next(p for p in picks if p.listing.id == last).reason)
            self.assertGreater([p.listing.id for p in offline].index(last), [p.listing.id for p in picks].index(last))
            self.assertIn("sie:stub", r.last_status)

            async def down(q, docs):
                raise OSError("SIE unreachable")
            r = SIERerank(SmartRanker(), score=down)
            picks = run("Fujifilm X100V", ranker=r)
        self.assertEqual([p.listing.id for p in picks], [p.listing.id for p in offline])
        self.assertIn("unavailable", r.last_status)


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
