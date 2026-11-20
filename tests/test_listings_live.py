"""Live listing source, tested only on recorded pages (no network)."""
import os
import unittest
from unittest import mock

from shopper.config import FIXTURES
from shopper.listings import BuyeeLiveSource, FixtureSource
from shopper.listings import yahoo
from shopper.listings.live import LiveSource
from shopper.listings.normalize import to_listing
from shopper.listings.polite import PoliteFetcher, RobotsDisallowed
from shopper.listings.query import parse_query, relevance
from shopper.models import Preferences, ShoppingRequest

SNAP = (FIXTURES / "snapshots" / "yahoo_search_x100v.html").read_text(encoding="utf-8")
SNAP_FROM = "https://auctions.yahoo.co.jp/search/search/X100V/0?fixed=0&b=1"
YAHOO_ROBOTS = "User-agent: *\nDisallow: /search/advanced\nDisallow: /search/*?*n=\n"
PREFS = Preferences("t")


def req(q="Fujifilm X100V", budget=900.0):
    return ShoppingRequest(q, budget, "US-CA")


class FakeWeb:
    """Answers like the real hosts did on 2026-11-20: Yahoo search 301 -> 200, Buyee robots 403,
    Off Mall robots disallow /search/. Records every URL and the fake clock time it was hit."""

    def __init__(self):
        self.t = 0.0
        self.hits: list[tuple[float, str]] = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def get(self, url, timeout):
        self.hits.append((self.t, url))
        self.t += 0.05
        if url == "https://auctions.yahoo.co.jp/robots.txt":
            return 200, url, YAHOO_ROBOTS
        if url.startswith("https://auctions.yahoo.co.jp/search/search?p="):
            return 200, SNAP_FROM, SNAP
        if url == "https://buyee.jp/robots.txt":
            return 403, url, ""
        if url == "https://netmall.hardoff.co.jp/robots.txt":
            return 200, url, "User-Agent: *\nDisallow: /search/\n"
        raise AssertionError(f"unexpected fetch {url}")


class YahooParseTest(unittest.TestCase):
    def setUp(self):
        self.rows = yahoo.parse_search(SNAP, fetched_from=SNAP_FROM, fetched_at="2026-11-20T22:00:00+00:00")
        self.by_id = {r["id"]: r for r in self.rows}

    def test_real_rows(self):
        self.assertEqual(len(self.rows), 22)
        r = self.by_id["yahoo_auctions:m1252200509"]
        self.assertEqual(r["url"], "https://auctions.yahoo.co.jp/jp/auction/m1252200509")
        self.assertEqual(r["price_jpy"], 198000)
        self.assertIn("X100V", r["title_ja"])
        self.assertEqual(r["condition_ja"], "目立った傷や汚れなし")
        self.assertTrue(r["photos"][0].startswith("https://"))
        self.assertEqual(r["fields"]["data_origin"], "live")
        self.assertEqual(r["fields"]["fetched_from"], SNAP_FROM)
        self.assertEqual(r["fields"]["fetched_at"], "2026-11-20T22:00:00+00:00")

    def test_flea_market_items(self):
        r = self.by_id["yahoo_fleamarket:z725429330"]
        self.assertEqual(r["url"], "https://paypayfleamarket.yahoo.co.jp/item/z725429330")
        self.assertEqual(r["fields"]["price_type"], "fixed price")

    def test_listing_conversion(self):
        l = to_listing(self.by_id["yahoo_auctions:m1252200509"])
        self.assertEqual((l.condition, l.category, l.source), ("good", "camera", "yahoo_auctions"))
        self.assertEqual(l.fields["condition_code"], "USED20")

    def test_not_a_search_page(self):
        with self.assertRaises(yahoo.ParseError):
            yahoo.parse_search("<html>captcha</html>", fetched_from="x")


class RelevanceOnLiveDataTest(unittest.TestCase):
    def test_search_url_and_lookalikes_do_not_match(self):
        plan = parse_query("Fujifilm X100V")
        ls = {r["id"]: to_listing(r) for r in yahoo.parse_search(SNAP, fetched_from=SNAP_FROM)}
        self.assertTrue(relevance(plan, ls["yahoo_auctions:m1252200509"]).ok)
        self.assertFalse(relevance(plan, ls["yahoo_auctions:l1252679228"]).ok)   # air con "ナノイーX 100V"
        self.assertFalse(relevance(plan, ls["yahoo_auctions:1252690577"]).ok)    # capacitors, URL has X100V


class PoliteFetcherTest(unittest.TestCase):
    def test_rate_limit_and_robots(self):
        web = FakeWeb()
        f = PoliteFetcher(get=web.get, sleep=web.sleep, clock=web.clock)
        final, page = f.fetch(yahoo.search_url("X100V"))
        self.assertEqual(final, SNAP_FROM)
        f.fetch(yahoo.search_url("X100F"))
        times = [t for t, _ in web.hits]
        self.assertTrue(all(b - a >= 1.0 for a, b in zip(times, times[1:])), times)
        self.assertEqual(sum(1 for _, u in web.hits if u.endswith("robots.txt")), 1)   # cached per host

    def test_unreadable_or_disallowing_robots_blocks(self):
        web = FakeWeb()
        f = PoliteFetcher(get=web.get, sleep=web.sleep, clock=web.clock)
        with self.assertRaises(RobotsDisallowed):
            f.fetch("https://buyee.jp/mercari/search?keyword=X100V")
        with self.assertRaises(RobotsDisallowed):
            f.fetch("https://auctions.yahoo.co.jp/search/advanced?p=X100V")
        self.assertNotIn("https://buyee.jp/mercari/search?keyword=X100V", [u for _, u in web.hits])


class LiveSourceTest(unittest.TestCase):
    def source(self, web):
        return LiveSource(FixtureSource(), fetcher=PoliteFetcher(get=web.get, sleep=web.sleep, clock=web.clock),
                          verbose=False)

    def test_live_returns_real_listings_with_provenance(self):
        web = FakeWeb()
        src = self.source(web)
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "1"}):
            out = src.search(req(), PREFS)
        self.assertTrue(out)
        self.assertTrue(all(l.fields["data_origin"] == "live" and l.fields["fetched_at"] for l in out))
        self.assertTrue(all(l.url.startswith(("https://auctions.yahoo.co.jp/jp/auction/",
                                              "https://paypayfleamarket.yahoo.co.jp/item/")) for l in out))
        self.assertIn("buyee not crawled", src.last_status)
        self.assertIn("hardoff not crawled", src.last_status)

    def test_fallback_is_labelled_fixture(self):
        def down(url, timeout):
            return 503, url, ""
        src = LiveSource(FixtureSource(), fetcher=PoliteFetcher(get=down, sleep=lambda s: None), verbose=False)
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "1"}):
            out = src.search(req(), PREFS)
        self.assertTrue(out)
        self.assertTrue(all(l.fields["data_origin"] == "fixture" for l in out))
        self.assertIn("FIXTURE samples (not real items)", src.last_status)

    def test_offline_never_fetches(self):
        def boom(url, timeout):
            raise AssertionError("network while offline")
        src = LiveSource(FixtureSource(), fetcher=PoliteFetcher(get=boom), verbose=False)
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": ""}):
            out = src.search(req(), PREFS)
        self.assertTrue(all(l.fields["data_origin"] == "fixture" for l in out))

    def test_default_buyee_source_delegates_to_live(self):
        web = FakeWeb()
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "1"}), \
                mock.patch("shopper.listings.live.PoliteFetcher",
                           lambda: PoliteFetcher(get=web.get, sleep=web.sleep, clock=web.clock)):
            out = BuyeeLiveSource(FixtureSource(), verbose=False).search(req(), PREFS)
        self.assertTrue(out and all(l.fields["data_origin"] == "live" for l in out))


if __name__ == "__main__":
    unittest.main()
