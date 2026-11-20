"""Vault: per-user address / sign-in sites / payment references; never secrets; file 0600."""
import os
import stat
import tempfile
import unittest
from pathlib import Path

from shopper.models import PaymentMethodRef, ShippingAddress
from shopper.vault import JsonVault, VaultError, check_secret_free, public_view

ADDR = ShippingAddress(name="Test User", line1="1 Test Way", city="Testville", postal_code="00000", country="us")


class Vault(unittest.TestCase):
    def setUp(self):
        self.v = JsonVault(Path(tempfile.mkdtemp()) / "vault" / "vault.json")

    def test_roundtrip_per_user_and_permissions(self):
        self.v.set_address("alice", ADDR)
        self.v.set_signed_in("alice", "https://www.Buyee.jp/cart", True)
        self.v.add_payment_method("alice", PaymentMethodRef("pm_abc123", "Visa", "4242", "12/30"))
        r = JsonVault(self.v.path).get("alice")
        self.assertEqual(r.address.country, "US")
        self.assertEqual(r.signed_in_sites, ["buyee.jp"])
        self.assertEqual((r.payment_methods[0].brand, r.default_payment), ("visa", "pm_abc123"))
        self.assertIsNone(JsonVault(self.v.path).get("bob").address)
        self.assertEqual(stat.S_IMODE(os.stat(self.v.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.v.path.parent).st_mode), 0o700)
        self.assertNotIn("token", str(public_view(r)))
        self.v.set_signed_in("alice", "buyee.jp", False)
        self.assertEqual(self.v.get("alice").signed_in_sites, [])

    def test_refuses_card_numbers_cvv_passwords(self):
        bad = [PaymentMethodRef("4242424242424242", "visa", "4242", "12/30"),
               PaymentMethodRef("pm_ok_1", "visa", "4242", "12/30", label="4242 4242 4242 4242"),
               PaymentMethodRef("pm_ok_1", "visa", "42", "12/30"),
               PaymentMethodRef("pm_ok_1", "visa", "4242", "2030-12"),
               PaymentMethodRef("pm_ok_1", "bitcoin", "4242", "12/30")]
        for pm in bad:
            with self.assertRaises(VaultError, msg=pm):
                self.v.add_payment_method("alice", pm)
        with self.assertRaises(VaultError):
            self.v.set_address("alice", ShippingAddress("x", "4111 1111 1111 1111", "c", "1", "US"))
        for d in ({"password": "x"}, {"cvv": "123"}, {"card_number": "1"}, {"a": {"cvc": "1"}}):
            with self.assertRaises(VaultError, msg=d):
                check_secret_free(d)
        check_secret_free({"postal_code": "94105", "phone": "+1 415 555 0100"})
        self.assertFalse(self.v.path.exists() and "4242424242424242" in self.v.path.read_text())

    def test_data_dir_is_gitignored(self):
        root = Path(__file__).resolve().parent.parent
        self.assertIn(".shopper-data/", (root / ".gitignore").read_text())


if __name__ == "__main__":
    unittest.main()
