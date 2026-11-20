"""Cost engine: researched rules, weight bands, method choice, duty, blocking, citations."""
import json
import os
import unittest
from unittest import mock

from shopper.config import FIXTURES
from shopper.cost import RulesCostEngine
from shopper.cost import senso
from shopper.models import Listing, ShoppingRequest

US = ShoppingRequest(query="x", max_total_usd=900, ship_to="US-CA")


def L(**kw):
    base = dict(id="t:1", source="mercari", url="u", title_ja="", title_en="", price_jpy=118000,
                condition="good", seller="s", ships_abroad=False, weight_kg=0.8, category="camera")
    base.update(kw)
    return Listing(**base)


class Rules(unittest.TestCase):
    def test_no_placeholder_and_every_rule_sourced(self):
        rules = json.loads((FIXTURES / "rules.json").read_text())
        self.assertNotIn("PLACEHOLDER", json.dumps(rules))
        for k, r in rules.items():
            if k.startswith("_"):
                continue
            self.assertRegex(r["as_of"], r"^\d{4}-\d{2}-\d{2}$", k)
            self.assertIn(r["status"], ("verified", "estimate"), k)
            if r["status"] == "verified":
                self.assertTrue(r["url"] and r["url"].startswith("https://"), k)
            else:
                self.assertTrue(r.get("tried"), f"{k}: estimate must say what was tried")


class Engine(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SHOPPER_LIVE", None)
        self.e = RulesCostEngine()

    def test_every_line_cited(self):
        c = self.e.landed_cost(L(), US)
        self.assertIsNone(c.blocked_reason)
        self.assertGreaterEqual(len(c.lines), 7)
        for line in c.lines:
            self.assertTrue(line.citation.source and line.citation.as_of, line.label)
            self.assertTrue(line.citation.url or "(estimate)" in line.citation.source, line.label)

    def test_ems_weight_band(self):
        # 0.8 kg + 0.2 packaging = 1.0 kg -> EMS 5,300 JPY; 1.01 kg -> 1.25 band 5,990
        fx = self.e.rules["fx_jpy_per_usd"]["value"]
        ship = lambda c: next(l for l in c.lines if l.label.startswith("international")).amount_usd  # noqa
        self.assertEqual(ship(self.e.landed_cost(L(weight_kg=0.8), US)), round(5300 / fx, 2))
        self.assertEqual(ship(self.e.landed_cost(L(weight_kg=0.81), US)), round(5990 / fx, 2))

    def test_method_policy(self):
        self.assertEqual(self.e.choose_method(1.0)[0], "ems")
        cheap = RulesCostEngine(policy="cheapest")
        self.assertEqual(cheap.choose_method(1.0)[0], "surface_small_packet")
        self.assertEqual(cheap.choose_method(2.5)[0], "ems")  # small packets stop at 2 kg
        self.assertIsNone(self.e.choose_method(40))

    def test_too_heavy_is_blocked(self):
        c = self.e.landed_cost(L(weight_kg=40), US)
        self.assertIn("cannot ship", c.blocked_reason)

    def test_duty_by_category(self):
        def duty(cat, price=100000):
            c = self.e.landed_cost(L(category=cat, price_jpy=price), US)
            return sum(l.amount_usd for l in c.lines if "duty" in l.label and "MFN" in l.label)
        item = round(100000 / self.e.rules["fx_jpy_per_usd"]["value"], 2)
        self.assertEqual(duty("camera"), 0.0)
        self.assertAlmostEqual(duty("apparel"), round(item * 0.165, 2), places=2)
        self.assertAlmostEqual(duty("watch"), round(0.40 + item * 0.085, 2), places=2)
        self.assertAlmostEqual(duty("something_new"), round(item * 0.05, 2), places=2)

    def test_de_minimis_suspended_small_items_pay(self):
        c = self.e.landed_cost(L(category="apparel", price_jpy=3000, weight_kg=0.3), US)
        self.assertGreater(sum(l.amount_usd for l in c.lines if "duty" in l.label), 0)

    def test_de_minimis_if_restored(self):
        self.e.rules["us_de_minimis"]["value"]["suspended"] = False
        c = self.e.landed_cost(L(category="apparel", price_jpy=3000), US)
        self.assertEqual(sum(l.amount_usd for l in c.lines if "duty" in l.label), 0)

    def test_non_us_duty_not_modeled(self):
        c = self.e.landed_cost(L(category="apparel"), ShoppingRequest("x", 900, "GB"))
        d = [l for l in c.lines if "duty" in l.label]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0].amount_usd, 0)
        self.assertIn("not modeled", d[0].label)

    def test_prohibited_blocked_with_source(self):
        for cat in ("battery_loose", "perfume", "alcohol", "aerosol"):
            c = self.e.landed_cost(L(category=cat), US)
            self.assertTrue(c.blocked_reason and c.blocked_reason.startswith("banned"), cat)
            self.assertIn("https://buyee.jp", c.blocked_reason)
            self.assertEqual(c.lines, [])
        c = self.e.landed_cost(L(fields={"lithium_batteries": "3"}), US)
        self.assertIn("lithium", c.blocked_reason)
        self.assertIsNone(self.e.landed_cost(L(fields={"lithium_batteries": "2"}), US).blocked_reason)

    def test_seller_paid_domestic_and_customs_fee(self):
        c = self.e.landed_cost(L(fields={"shipping": "送料込み"}), US)
        self.assertEqual(next(l for l in c.lines if l.label.startswith("domestic")).amount_usd, 0)
        self.assertFalse(any("customs clearance" in l.label for l in c.lines))
        c = self.e.landed_cost(L(price_jpy=210000), US)
        self.assertTrue(any("customs clearance" in l.label for l in c.lines))

    def test_fixture_x100v_total(self):
        # 118,000 JPY camera, 0.8kg -> fixed expectation from the researched rules
        r = self.e.rules
        fx = r["fx_jpy_per_usd"]["value"]
        u = lambda j: round(j / fx, 2)  # noqa
        item = u(118000)
        expect = item + u(500) + u(500) + u(0) + u(750) + u(5300) + 0.0 + round(item * 0.10, 2)
        self.assertAlmostEqual(self.e.landed_cost(L(), US).total_usd, round(expect, 2), places=2)


class Senso(unittest.TestCase):
    def test_offline_returns_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(senso.fetch_rules())
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "1"}, clear=True):
            self.assertIsNone(senso.fetch_rules())  # no key -> no call

    def test_live_failure_falls_back_to_file(self):
        env = {"SHOPPER_LIVE": "1", "SENSO_API_KEY": "k", "SENSO_RULES_CONTENT_ID": "c",
               "SENSO_BASE_URL": "http://127.0.0.1:9"}
        with mock.patch.dict(os.environ, env, clear=True):
            e = RulesCostEngine()
        self.assertEqual(e.prefix, "fixtures/rules.json")

    def test_live_reads_rules_from_senso(self):
        rules = json.loads((FIXTURES / "rules.json").read_text())
        rules["fx_jpy_per_usd"]["value"] = 100.0
        env = {"SHOPPER_LIVE": "1", "SENSO_API_KEY": "k", "SENSO_RULES_CONTENT_ID": "c"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(senso, "_extract", wraps=senso._extract), \
                mock.patch("urllib.request.urlopen") as op:
            op.return_value.__enter__.return_value.read.return_value = \
                json.dumps({"id": "c", "text": json.dumps(rules)}).encode()
            e = RulesCostEngine()
        self.assertEqual(e.prefix, "senso:japan-landed-cost-rules")
        c = e.landed_cost(L(), US)
        self.assertTrue(c.lines[0].citation.source.startswith("senso:"))
        self.assertEqual(c.fx_jpy_per_usd, 100.0)


if __name__ == "__main__":
    unittest.main()
