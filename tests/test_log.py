import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from getplaybook import log


class StatsTakeRate(unittest.TestCase):
    def stats(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.jsonl"
            path.write_text("".join(json.dumps({"playbook": n, "event": e}) + "\n" for n, e in rows), encoding="utf-8")
            with mock.patch.object(log, "_path", return_value=path):
                return {line.split()[0]: line for line in log.stats()}

    def test_low_take_marks_rarely_followed_playbooks_only(self):
        lines = self.stats([("a", "offer")] * 10 + [("a", "take")] + [("b", "offer")] * 10 + [("b", "take")] * 8 + [("c", "deny")])
        self.assertIn("take rate 10%", lines["a"])
        self.assertTrue(lines["a"].endswith(" LOW TAKE"))
        self.assertIn("take rate 80%", lines["b"])
        self.assertNotIn("LOW TAKE", lines["b"])
        self.assertNotIn("take rate", lines["c"])

    def test_low_take_needs_five_offers(self):
        self.assertNotIn("LOW TAKE", self.stats([("a", "offer")] * 4)["a"])


if __name__ == "__main__":
    unittest.main()
