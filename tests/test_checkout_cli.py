import contextlib
import io
import os
import re
import tempfile
import unittest
from unittest import mock


class CLI(unittest.TestCase):
    def run_cli(self, *args):
        from shopper.cli_checkout import main
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(list(args))
        return rc, buf.getvalue()

    def test_buy_then_approve_across_processes_and_memory(self):
        with mock.patch.dict(os.environ, {"SHOPPER_DATA_DIR": tempfile.mkdtemp()}):
            import importlib
            import shopper.config, shopper.memory, shopper.checkout  # noqa: E401
            importlib.reload(shopper.config); importlib.reload(shopper.memory); importlib.reload(shopper.checkout)
            import shopper.app; importlib.reload(shopper.app)
            rc, out = self.run_cli("buy", "Fujifilm X100V", "--budget", "900")
            self.assertIn("STOP: waiting for human approval", out)
            self.assertIn("Nothing has been paid", out)
            tok = re.search(r"shopper approve (\S+)", out).group(1)
            rc, out = self.run_cli("approve", tok)
            self.assertEqual(rc, 0, out); self.assertIn("placed", out)
            rc, out = self.run_cli("approve", tok)
            self.assertEqual(rc, 1); self.assertIn("blocked", out)
            rc, out = self.run_cli("memory")
            self.assertIn("mercari:m001", out)
            rc, out = self.run_cli("sessions")
            self.assertIn("placed", out)


if __name__ == "__main__":
    unittest.main()
