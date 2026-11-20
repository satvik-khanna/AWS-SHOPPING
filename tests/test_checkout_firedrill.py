import json
import tempfile
import unittest
from pathlib import Path

from firedrill.run import SCENARIOS, main


class Firedrill(unittest.TestCase):
    def test_every_scenario_holds_the_gate(self):
        out = Path(tempfile.mkdtemp())
        rc = main(["-n", str(len(SCENARIOS) * 2), "--out", str(out)])
        rep = json.loads((out / "report.json").read_text())
        bad = [r for r in rep["results"] if not r["ok"]]
        self.assertEqual(rc, 0, bad)
        self.assertEqual({r["scenario"] for r in rep["results"]}, set(SCENARIOS))
        inj = [r for r in rep["results"] if r["scenario"] == "prompt_injection"]
        self.assertTrue(all("flagged=True" in " ".join(r["events"]) for r in inj))
        self.assertIn("invariant held", (out / "report.md").read_text())


if __name__ == "__main__":
    unittest.main()
