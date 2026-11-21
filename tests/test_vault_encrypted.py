"""Encrypted Touch ID vault: ciphertext at rest, Touch ID required to read, tamper detected."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from shopper.models import ShippingAddress
from shopper.vault import VaultError
from shopper.vault.encrypted import EncryptedTouchIDVault, TouchIDDenied

KEY = bytes(range(32))
ADDR = dict(name="Test Person", line1="1 Test St", city="Testville", region="CA",
            postal_code="94000", country="US", phone="5550000000")


def _addr() -> ShippingAddress:
    import dataclasses
    names = {f.name for f in dataclasses.fields(ShippingAddress)}
    return ShippingAddress(**{k: v for k, v in ADDR.items() if k in names})


class EncryptedVaultTest(unittest.TestCase):
    def setUp(self):
        self.td = TemporaryDirectory()
        self.path = Path(self.td.name) / "v.enc"
        self.calls: list[str] = []

    def tearDown(self):
        self.td.cleanup()

    def _vault(self, allow=True, key=KEY):
        def auth(reason):
            self.calls.append(reason)
            if not allow:
                raise TouchIDDenied("denied")
        return EncryptedTouchIDVault(self.path, key=key, auth=auth)

    def test_round_trip_needs_touch_id_and_is_ciphertext_on_disk(self):
        v = self._vault()
        v.set_address("u1", _addr())
        v.set_login("u1", "buyee.jp", "me@example.com", "hunter2-secret")
        raw = self.path.read_bytes()
        for plain in (b"hunter2-secret", b"1 Test St", b"me@example.com"):
            self.assertNotIn(plain, raw)
        self.assertEqual(self.calls, [])                 # writes/status need no Touch ID
        self.assertTrue(v.status("u1")["has_address"])
        self.assertEqual(v.unlock_address("u1").line1, "1 Test St")
        self.assertEqual(v.unlock_login("u1", "buyee.jp")["password"], "hunter2-secret")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")

    def test_denied_touch_id_reads_nothing(self):
        self._vault().set_address("u1", _addr())
        with self.assertRaises(TouchIDDenied):
            self._vault(allow=False).unlock_address("u1")

    def test_wrong_key_or_tamper_is_refused(self):
        self._vault().set_address("u1", _addr())
        with self.assertRaises(VaultError):
            self._vault(key=bytes(32)).unlock_address("u1")
        b = bytearray(self.path.read_bytes()); b[-3] ^= 1
        self.path.write_bytes(bytes(b))
        with self.assertRaises((VaultError, ValueError)):
            self._vault().unlock_address("u1")


if __name__ == "__main__":
    unittest.main()
