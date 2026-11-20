"""Payments (P1): swipe grant required, amount == approved landed total, idempotent, test keys only."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from shopper.checkout.core import BuyeeCheckout
from shopper.checkout.drivers import SimBuyeeDriver
from shopper.checkout.store import InMemorySessionStore
from shopper.cost import RulesCostEngine
from shopper.models import Listing, RankedPick, ShoppingRequest
from shopper.payments import KINDS, LiveKeyRefused, build_provider
from shopper.payments import backends as B
from shopper.payments.core import Ledger


class GrabDriver(SimBuyeeDriver):
    """Sim driver that keeps the ApprovalGrant the approval gate hands to place()."""
    def __init__(self):
        super().__init__()
        self.grants = {}

    def place(self, session_id, grant):
        self.grants[session_id] = grant
        return super().place(session_id, grant)


def listing(lid="mercari:m001"):
    return Listing(id=lid, source="mercari", url="https://jp.mercari.com/item/m001", title_ja="x", title_en="x",
                   price_jpy=118000, condition="like_new", seller="s", ships_abroad=False, weight_kg=0.8,
                   category="camera")


class Flow(unittest.TestCase):
    def setUp(self):
        os.environ.pop("SHOPPER_LIVE", None)
        self.store = InMemorySessionStore()
        self.driver = GrabDriver()
        self.co = BuyeeCheckout(driver=self.driver, store=self.store)
        l = listing()
        cost = RulesCostEngine().landed_cost(l, ShoppingRequest("x", 900, "US-CA"))
        self.total = cost.total_usd
        self.cart = self.co.fill_cart(RankedPick(l, cost, 1.0, "test"))
        self.assertEqual(self.cart.status, "awaiting_approval")
        self.sid = self.cart.session_id
        self.ledger = Ledger()
        self.p = {k: build_provider(k, sessions=self.store, ledger=self.ledger) for k in KINDS}

    def swipe(self):
        self.assertEqual(self.co.approve(self.cart.approval_token).status, "placed")
        return self.driver.grants[self.sid]

    def intent(self, kind="card", amount=None, key="k1", ref=None):
        p = self.p[kind]
        return p.create_intent("u", self.total if amount is None else amount, ref or p.methods("u")[0].token, key,
                               cart_session_id=self.sid)

    def test_methods_are_references_only(self):
        for k in KINDS:
            ms = self.p[k].methods("u")
            self.assertTrue(ms)
            for m in ms:
                self.assertEqual(m.kind, k)
                self.assertTrue(m.last4 == "" or (len(m.last4) == 4 and m.last4.isdigit()))
        self.assertTrue(self.p["card"].methods("u")[0].token.startswith("pm_"))

    def test_happy_path_all_kinds_charge_once(self):
        for i, k in enumerate(KINDS):
            with self.subTest(k):
                self.setUp()
                it = self.intent(k)
                self.assertEqual(it.status, "requires_approval")
                self.assertEqual(self.p[k].backend.charges, [])        # creating charges nothing
                done = self.p[k].confirm(it, self.swipe())
                self.assertEqual(done.status, "succeeded", done.detail)
                self.assertEqual(len(self.p[k].backend.charges), 1)

    def test_confirm_refuses_without_grant(self):
        it = self.intent()
        for bad in (None, "grant_forged", object()):
            r = self.p["card"].confirm(it, bad)
            self.assertEqual(r.status, "blocked")
            self.assertIn("ApprovalGrant", r.detail)
        self.assertEqual(self.p["card"].backend.charges, [])

    def test_confirm_refuses_before_swipe_even_with_grant_object(self):
        from shopper.checkout.machine import _mint_grant
        it = self.intent()
        r = self.p["card"].confirm(it, _mint_grant(self.sid))       # before the swipe
        self.assertEqual(r.status, "blocked")
        self.swipe()
        r = self.p["card"].confirm(it, _mint_grant(self.sid))       # after the swipe, but not the gate's grant
        self.assertEqual(r.status, "blocked")
        self.assertIn("not recorded", r.detail)
        self.assertEqual(self.p["card"].backend.charges, [])

    def test_grant_for_other_cart_refused(self):
        other = self.co.fill_cart(RankedPick(listing("mercari:m009"),
                                             RulesCostEngine().landed_cost(listing("mercari:m009"),
                                                                           ShoppingRequest("x", 900, "US")), 1, "t"))
        self.co.approve(other.approval_token)
        it = self.intent()
        r = self.p["card"].confirm(it, self.driver.grants[other.session_id])
        self.assertEqual(r.status, "blocked")
        self.assertIn("different cart", r.detail)

    def test_amount_mismatch_blocks(self):
        it = self.intent(amount=self.total - 0.01)
        self.assertEqual(it.status, "blocked")
        self.assertIn("landed total", it.detail)
        r = self.p["card"].confirm(it, self.swipe())
        self.assertEqual(r.status, "blocked")
        self.assertEqual(self.p["card"].backend.charges, [])

    def test_double_swipe_charges_once(self):
        it = self.intent()
        g = self.swipe()
        a = self.p["card"].confirm(it, g)
        b = self.p["card"].confirm(it, g)
        self.assertEqual((a.status, b.status), ("succeeded", "succeeded"))
        self.assertEqual(a.id, b.id)
        self.assertEqual(len(self.p["card"].backend.charges), 1)
        # same key again -> same intent; new key or other kind for the same cart -> blocked
        self.assertEqual(self.intent().id, it.id)
        self.assertEqual(self.intent(key="k2").status, "blocked")
        self.assertEqual(self.intent("paypal", key="k3").status, "blocked")

    def test_key_reuse_with_other_params_blocked(self):
        self.intent()
        self.assertEqual(self.intent(ref="pm_card_visa_chargeDeclined").status, "blocked")

    def test_decline_then_cancel(self):
        it = self.intent(ref="pm_card_visa_chargeDeclined")
        r = self.p["card"].confirm(it, self.swipe())
        self.assertEqual(r.status, "failed")
        self.assertEqual(self.p["card"].cancel(r).status, "canceled")

    def test_file_ledger_survives_new_process(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.json"
            p1 = build_provider("card", sessions=self.store, ledger=Ledger(path))
            it = p1.create_intent("u", self.total, "pm_card_visa", "kf", cart_session_id=self.sid)
            self.assertEqual(p1.confirm(it, self.swipe()).status, "succeeded")
            p2 = build_provider("card", sessions=self.store, ledger=Ledger(path))
            self.assertEqual(p2.confirm(it, self.driver.grants[self.sid]).status, "succeeded")
            self.assertEqual(p2.backend.charges, [])
            self.assertNotIn("4242424242424242", path.read_text())


class LiveKeys(unittest.TestCase):
    def test_offline_and_missing_keys_use_fake(self):
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "1"}, clear=True):
            for k in KINDS:
                self.assertIsInstance(build_provider(k, sessions={}, ledger=Ledger()).backend, B.FakeBackend)

    def test_live_stripe_key_refused(self):
        for key in ("sk_live_abc", "rk_live_abc", "pk_test_abc", "whatever"):
            with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "1", "STRIPE_SECRET_KEY": key}, clear=True):
                with self.assertRaises(LiveKeyRefused):
                    build_provider("card", sessions={}, ledger=Ledger())

    def test_paypal_live_env_refused(self):
        env = {"SHOPPER_LIVE": "1", "PAYPAL_CLIENT_ID": "a", "PAYPAL_CLIENT_SECRET": "b", "PAYPAL_ENV": "live"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(LiveKeyRefused):
                build_provider("venmo", sessions={}, ledger=Ledger())

    def test_stripe_request_shape(self):
        calls = []

        def fake(method, url, headers, body=None):
            calls.append((method, url, headers, body))
            return {"id": "pi_123", "status": "requires_confirmation" if url.endswith("intents") else "succeeded"}
        with mock.patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_x"}, clear=True):
            be = B.StripeBackend("card", opener=fake)
        from shopper.models import PaymentIntent
        it = PaymentIntent("pi_local_1", "card", "u", 874.14, "pm_card_visa", "key1", "sess1")
        it = be.create(it)
        it = be.charge(it, None)
        (m1, u1, h1, b1), (m2, u2, h2, _) = calls
        self.assertEqual(u1, "https://api.stripe.com/v1/payment_intents")
        self.assertIn(b"amount=87414", b1)
        self.assertIn(b"confirm=false", b1)
        self.assertEqual(h1["Idempotency-Key"], "create-key1")
        self.assertEqual(u2, "https://api.stripe.com/v1/payment_intents/pi_123/confirm")
        self.assertEqual(h2["Idempotency-Key"], "confirm-key1")
        self.assertEqual(it.status, "succeeded")

    def test_paypal_request_shape_sandbox_only(self):
        calls = []

        def fake(method, url, headers, body=None):
            calls.append((method, url, headers, body))
            if url.endswith("/oauth2/token"):
                return {"access_token": "T"}
            if url.endswith("/capture"):
                return {"status": "COMPLETED", "purchase_units": [{"payments": {"captures": [
                    {"status": "COMPLETED", "amount": {"value": "874.14"}}]}}]}
            return {"id": "ORDER1", "status": "CREATED", "links": [{"rel": "payer-action", "href": "https://x"}]}
        env = {"PAYPAL_CLIENT_ID": "a", "PAYPAL_CLIENT_SECRET": "b"}
        with mock.patch.dict(os.environ, env, clear=True):
            be = B.PayPalBackend("venmo", opener=fake)
            from shopper.models import PaymentIntent
            it = be.create(PaymentIntent("pi_local_1", "venmo", "u", 874.14, "venmo:checkout", "key1", "sess1"))
            self.assertEqual(it.status, "payer_action_required")
            it = be.charge(it, None)
        self.assertEqual(it.status, "succeeded")
        self.assertTrue(all(c[1].startswith("https://api-m.sandbox.paypal.com/") for c in calls))
        order = json.loads(calls[1][3])
        self.assertEqual(order["purchase_units"][0]["amount"], {"currency_code": "USD", "value": "874.14"})
        self.assertLessEqual(len(calls[1][2]["PayPal-Request-Id"]), 38)


if __name__ == "__main__":
    unittest.main()
