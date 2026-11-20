import tempfile
import unittest
from pathlib import Path

from shopper.memory import JsonMemory
from shopper.memory.feedback import apply_feedback
from shopper.models import Listing


def L(i="m1", seller="s", condition="good"):
    return Listing(id=f"mercari:{i}", source="mercari", url="u", title_ja="t", title_en="T",
                   price_jpy=1000, condition=condition, seller=seller, ships_abroad=False)


class Memory(unittest.TestCase):
    def setUp(self):
        self.m = JsonMemory(Path(tempfile.mkdtemp()) / "mem.json")

    def test_persist_all_fields(self):
        self.m.remember_purchase("u", L())
        self.m.remember_purchase("u", L())
        self.m.reject_seller("u", "bad")
        self.m.reject_seller("u", "bad")
        self.m.set_condition_min("u", "like_new")
        self.m.set_size("u", "shoes", "27cm")
        p = JsonMemory(self.m.path).recall("u")
        self.assertEqual(p.past_purchases, ["mercari:m1"])
        self.assertEqual(p.rejected_sellers, ["bad"])
        self.assertEqual(p.condition_min, "like_new")
        self.assertEqual(p.sizes, {"shoes": "27cm"})
        self.assertEqual(JsonMemory(self.m.path).recall("other").past_purchases, [])

    def test_bad_condition_rejected(self):
        with self.assertRaises(ValueError):
            self.m.set_condition_min("u", "mint")

    def test_corrupt_file_recovers(self):
        self.m.path.parent.mkdir(parents=True, exist_ok=True)
        self.m.path.write_text("{not json")
        self.assertEqual(self.m.recall("u").user_id, "u")

    def test_feedback(self):
        apply_feedback(self.m, "u", "reject", vars(L(seller="x", condition="fair")), reason="too worn")
        p = self.m.recall("u")
        self.assertEqual(p.condition_min, "good")
        self.assertEqual(p.rejected_sellers, [])
        apply_feedback(self.m, "u", "reject", vars(L(seller="x")), reject_seller=True)
        self.assertEqual(self.m.recall("u").rejected_sellers, ["x"])
        apply_feedback(self.m, "u", "approve", vars(L("m9")))
        self.assertIn("mercari:m9", self.m.recall("u").past_purchases)


if __name__ == "__main__":
    unittest.main()
