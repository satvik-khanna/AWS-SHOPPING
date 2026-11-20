"""C1: Chrome / computer-use flow up to the payment step, swipe = approve, vault, dt-ax safety."""
import os
import unittest
from unittest import mock

from shopper.checkout import BuyeeCheckout, FakeChromeDriver
from shopper.checkout import machine as M
from shopper.checkout.don_tacos_cua import DonTacosChromeDriver, ax_path
from shopper.checkout.guards import UnsafeUrl, check_open_url
from shopper.models import PaymentMethodRef, ShippingAddress
from shopper.vault import MemoryVault
from tests.test_checkout_machine import pick

ADDR = ShippingAddress(name="Test User", line1="1 Test Way", city="Testville", postal_code="00000",
                       country="US", region="CA")
PM = PaymentMethodRef(token="pm_test_visa", brand="visa", last4="4242", exp="12/30")


def make(signed_in=None, user="alice", t=None, **kw):
    v = MemoryVault()
    v.set_address(user, ADDR)
    v.add_payment_method(user, PM)
    d = FakeChromeDriver(signed_in_sites=signed_in)
    clock = (lambda: t[0]) if t else None
    co = BuyeeCheckout(driver=d, vault=v, user_id=user, **({"clock": clock} if clock else {}), **kw)
    return co, d, v


class ChromeFlow(unittest.TestCase):
    def test_stops_at_payment_step_with_vault_data(self):
        co, d, v = make()
        r = co.fill_cart(pick())
        self.assertEqual(r.status, "awaiting_approval")
        s = co.session(r.session_id)
        self.assertEqual(s.state, M.AWAITING_SWIPE)
        states = [st.state for st in s.steps]
        for want in (M.LISTING_OPENED, M.ADDED_TO_CART, M.CART_FILLED, M.CHECKOUT_OPENED, M.SHIPPING_FILLED, M.AWAITING_SWIPE):
            self.assertIn(want, states)
        self.assertEqual(d.orders, [])
        self.assertEqual(d.tabs[r.session_id]["shipping"]["city"], "Testville")
        self.assertEqual(s.payment, {"brand": "visa", "last4": "4242", "exp": "12/30"})
        self.assertNotIn("pm_test_visa", str(s.to_dict()))          # the provider token is not in the log
        self.assertNotIn("1 Test Way", " ".join(s.transcript()))     # address values are not in the log
        self.assertIn("buyee.jp", v.get("alice").signed_in_sites)
        self.assertEqual(co.approve(r.approval_token, user_id="alice").status, "placed")
        self.assertEqual(len(d.orders), 1)
        self.assertEqual(co.approve(r.approval_token, user_id="alice").status, "blocked")
        self.assertEqual(len(d.orders), 1)

    def test_needs_sign_in_stops_without_token(self):
        co, d, v = make(signed_in=set())
        r = co.fill_cart(pick())
        s = co.session(r.session_id)
        self.assertEqual((r.status, s.state), ("blocked", M.NEEDS_SIGN_IN))
        self.assertIsNone(r.approval_token)
        self.assertIn("Sign in to buyee.jp", s.next_step)
        self.assertEqual(d.typed, [])
        self.assertNotIn("buyee.jp", v.get("alice").signed_in_sites)

    def test_needs_address_when_live_and_none_saved(self):
        d = FakeChromeDriver()
        co = BuyeeCheckout(driver=d, vault=MemoryVault(), user_id="bob")
        with mock.patch.dict(os.environ, {"SHOPPER_LIVE": "1"}):
            r = co.fill_cart(pick())
        self.assertEqual(co.session(r.session_id).state, M.NEEDS_ADDRESS)
        self.assertIsNone(r.approval_token)

    def test_other_user_cannot_use_or_burn_token(self):
        co, d, _ = make()
        r = co.fill_cart(pick())
        self.assertIn("another user", co.approve(r.approval_token, user_id="mallory").detail)
        self.assertEqual(co.reject(r.approval_token, user_id="mallory").status, "blocked")
        self.assertEqual(d.orders, [])
        self.assertEqual(co.approve(r.approval_token, user_id="alice").status, "placed")

    def test_token_expires(self):
        t = [1000.0]
        co, d, _ = make(t=t, ttl=60)
        r = co.fill_cart(pick())
        t[0] += 61
        self.assertEqual(co.approve(r.approval_token, user_id="alice").status, "blocked")
        self.assertEqual(co.session(r.session_id).state, M.EXPIRED)
        self.assertEqual(d.orders, [])

    def test_price_change_on_site_blocks(self):
        co, d, _ = make()
        r = co.fill_cart(pick())
        d.reprice(r.session_id, d.tabs[r.session_id]["price_jpy"] + 1)
        res = co.approve(r.approval_token, user_id="alice")
        self.assertEqual(res.status, "blocked")
        self.assertIn("price on the site changed", res.detail)
        self.assertEqual(d.orders, [])

    def test_amount_tampered_in_store_blocks(self):
        co, d, _ = make()
        r = co.fill_cart(pick())
        s = co.store.get(r.session_id)
        s.total_usd += 1
        co.store.save(s)
        self.assertEqual(co.approve(r.approval_token, user_id="alice").status, "blocked")
        self.assertEqual(d.orders, [])

    def test_no_place_without_grant(self):
        co, d, _ = make()
        r = co.fill_cart(pick())
        for bad in (None, "grant", object()):
            with self.assertRaises(PermissionError):
                d.place(r.session_id, bad)
        self.assertEqual(d.orders, [])

    def test_unsafe_listing_url_blocked_in_log(self):
        co, d, _ = make()
        for url in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", "-a", "https://", "ftp://x.jp/a"):
            r = co.fill_cart(pick(fields={"buyee_url": url}))
            s = co.session(r.session_id)
            self.assertEqual(s.state, M.BLOCKED, url)
            self.assertTrue(any(st.action == "unsafe URL refused" for st in s.steps), url)
        self.assertEqual(d.tabs, {})


class FakeAX:
    """Stands in for dt-ax: records every command, serves a canned accessibility tree."""
    def __init__(self, elements):
        self.calls = []
        self.elements = elements

    def __call__(self, args):
        self.calls.append(list(args))
        if args[0] == "open-url":
            return {"pid": 42, "id": 7, "title": "t"}
        if args[0] == "tree":
            return {"windows": [{"elements": self.elements}]}
        return {"ok": True}


class DonTacosSafety(unittest.TestCase):
    def test_add_to_cart_never_presses_buy_now(self):
        ax = FakeAX([{"role": "AXButton", "label": "Buy Now", "ref": "b1"},
                     {"role": "AXButton", "label": "購入手続きへ", "ref": "b2"}])
        d = DonTacosChromeDriver(run=ax)
        d.open_listing("s1", {"id": "mercari:m1", "source": "mercari"})
        with self.assertRaises(RuntimeError):
            d.add_to_cart("s1", {})
        self.assertFalse([c for c in ax.calls if c[0] == "press"])
        ax.elements.append({"role": "AXButton", "label": "Add to Cart", "ref": "b3"})
        d.add_to_cart("s1", {})
        self.assertEqual([c[2] for c in ax.calls if c[0] == "press"], ["b3"])

    def test_open_listing_rejects_unsafe_url_before_dt_ax(self):
        ax = FakeAX([])
        d = DonTacosChromeDriver(run=ax)
        for url in ("javascript:alert(1)", "file:///etc/passwd", "data:,x", "--help", "-x", "about:blank", "https:///nohost"):
            with self.assertRaises(UnsafeUrl, msg=url):
                d.open_listing("s", {"id": "other:x", "source": "other", "fields": {"buyee_url": url}})
        self.assertEqual(ax.calls, [])
        self.assertEqual(check_open_url("https://buyee.jp/mercari/item/m1"), "https://buyee.jp/mercari/item/m1")

    def test_place_needs_grant_and_explicit_env(self):
        ax = FakeAX([{"role": "AXButton", "label": "Place order", "ref": "p1"}])
        d = DonTacosChromeDriver(run=ax)
        d.open_listing("abc", {"id": "mercari:m1", "source": "mercari"})
        with self.assertRaises(PermissionError):
            d.place("abc", None)
        g = M._mint_grant("abc")
        with mock.patch.dict(os.environ, {"SHOPPER_ALLOW_LIVE_ORDER": "0"}):
            self.assertTrue(d.place("abc", g).startswith("HANDOFF:"))
        self.assertFalse([c for c in ax.calls if c[0] == "press"])

    def test_fill_shipping_skips_secret_fields(self):
        ax = FakeAX([{"role": "AXTextField", "label": "Card number", "ref": "c"},
                     {"role": "AXTextField", "label": "Password", "ref": "p", "secure": True},
                     {"role": "AXTextField", "label": "City", "ref": "city"}])
        d = DonTacosChromeDriver(run=ax)
        d.open_listing("s", {"id": "mercari:m1", "source": "mercari"})
        self.assertEqual(d.fill_shipping("s", {"city": "Testville"}), ["city"])
        self.assertEqual([c[2] for c in ax.calls if c[0] == "set"], ["city"])

    def test_ax_path_has_no_personal_layout(self):
        import inspect
        from shopper.checkout import don_tacos_cua
        src = inspect.getsource(don_tacos_cua)
        self.assertNotIn("workspaces", src)
        self.assertNotIn('"orca"', src)
        with mock.patch.dict(os.environ, {"DT_AX": "/nonexistent/dt-ax"}):
            self.assertIsNone(ax_path())


if __name__ == "__main__":
    unittest.main()
