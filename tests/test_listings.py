import json
import os
import unittest
from collections import Counter
from unittest import mock

from shopper.config import FIXTURES
from shopper.listings import (BuyeeLiveSource, FixtureSource, expand_query, extract_fields,
                              normalize_condition, parse_query)
from shopper.listings.buyee import parse_mercari_search, parse_yahoo_search
from shopper.models import Preferences, ShoppingRequest

PREFS = Preferences("t")


def req(q, budget=900.0):
    return ShoppingRequest(q, budget, "US-CA")


class FixtureSetTest(unittest.TestCase):
    def test_fixture_set_is_rich(self):
        raw = json.loads((FIXTURES / "listings.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(raw), 30)
        self.assertEqual(len({r["id"] for r in raw}), len(raw))
        cats = Counter(l.category for l in FixtureSource().all())
        for c in ("camera", "sneakers", "vinyl", "figure"):
            self.assertGreaterEqual(cats[c], 5, c)
        self.assertEqual({r["source"] for r in raw}, {"mercari", "yahoo_auctions"})
        self.assertTrue(all(any("\u3040" <= ch <= "\u9fff" for ch in r["title_ja"]) for r in raw))


class QueryTest(unittest.TestCase):
    def test_expansion_to_japanese(self):
        self.assertIn("富士フイルム X100V", expand_query("Fujifilm X100V"))
        self.assertTrue(any("ダンク" in k for k in expand_query("Nike Dunk Low")))

    def test_budget_and_size_are_not_keywords(self):
        plan = parse_query("used Nike Dunk Low size 27cm under $300")
        self.assertEqual(plan.size_cm, 27.0)
        self.assertFalse(any("300" in c.key for c in plan.concepts))

    def test_model_token_is_required(self):
        ids = [l.id for l in FixtureSource().search(req("Fujifilm X100V"), PREFS)]
        self.assertIn("mercari:m001", ids)
        titles = [l.title_ja for l in FixtureSource().search(req("Fujifilm X100V"), PREFS)]
        self.assertTrue(all("X100V" in t.upper().replace(" ", "") for t in titles))
        self.assertFalse(any("X100VI" in t for t in titles))


class NormalizeTest(unittest.TestCase):
    def test_condition_grades(self):
        self.assertEqual(normalize_condition("目立った傷や汚れなし")[0], "good")
        self.assertEqual(normalize_condition("新品、未使用")[0], "new")
        self.assertEqual(normalize_condition("未使用に近い")[0], "like_new")
        self.assertEqual(normalize_condition("やや傷や汚れあり")[0], "fair")
        self.assertEqual(normalize_condition("全体的に状態が悪い")[0], "junk")

    def test_junk_title_beats_grade(self):
        self.assertEqual(normalize_condition("目立った傷や汚れなし", "ジャンク 部品取り")[0], "junk")

    def test_field_extraction(self):
        f = extract_fields("ナイキ ダンク ロー パンダ 27cm 黒", "シャッター回数 3,200回 元箱付き 動作確認済み")
        self.assertEqual(f["size_cm"], "27")
        self.assertEqual(f["shutter_count"], "3200")
        self.assertEqual(f["box"], "yes")
        self.assertEqual(f["tested"], "working")

    def test_accessory_flag(self):
        self.assertEqual(extract_fields("FUJIFILM X100V 空箱のみ 本体なし").get("item_type"), "accessory")
        self.assertNotIn("item_type", extract_fields("富士フイルム X100V シルバー 美品"))


MERCARI_HTML = """<ul class="item-lists"><li class="list"><a href="/mercari/item/m999?x=1">
<h2 class="name">富士フイルム X100V 美品</h2><p class="price">120,000 YEN</p></a></li></ul>"""
YAHOO_HTML = """<li class="itemCard"><div class="itemCard__itemName"><a href="/item/jdirectitems/auction/x123">
FUJIFILM X100V ジャンク</a></div><span class="g-title">Current Price</span><span class="g-price">50,000 YEN</span></li>"""


class LiveSourceTest(unittest.TestCase):
    def test_parsers(self):
        m = parse_mercari_search(MERCARI_HTML)
        self.assertEqual((m[0]["id"], m[0]["price_jpy"]), ("mercari:m999", 120000))
        y = parse_yahoo_search(YAHOO_HTML)
        self.assertEqual((y[0]["id"], y[0]["price_jpy"]), ("yahoo_auctions:x123", 50000))

    def test_offline_never_fetches(self):
        def boom(url):
            raise AssertionError("fetched while offline")
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": ""}):
            out = BuyeeLiveSource(FixtureSource(), fetch=boom, verbose=False).search(req("Fujifilm X100V"), PREFS)
        self.assertTrue(out)

    def test_live_uses_pages_and_falls_back_on_error(self):
        pages = {"mercari": MERCARI_HTML, "item/search": YAHOO_HTML}
        fetch = lambda url: next(v for k, v in pages.items() if k in url)
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "1"}):
            out = BuyeeLiveSource(FixtureSource(), fetch=fetch, verbose=False).search(req("Fujifilm X100V"), PREFS)
            self.assertEqual({l.id for l in out}, {"mercari:m999", "yahoo_auctions:x123"})
            self.assertEqual([l.condition for l in out if l.id.endswith("x123")], ["junk"])

            def fail(url):
                raise OSError("WAF challenge")
            src = BuyeeLiveSource(FixtureSource(), fetch=fail, verbose=False)
            out = src.search(req("Fujifilm X100V"), PREFS)
        self.assertIn("mercari:m001", {l.id for l in out})
        self.assertIn("fixtures", src.last_status)


if __name__ == "__main__":
    unittest.main()
