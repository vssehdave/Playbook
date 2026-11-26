import json
import tempfile
import unittest
from pathlib import Path

from helpers import mine, playbook  # noqa: F401


def _session(*items) -> list:
    """Read a session of ("user", text) and ("tool", name, input) items, short turns kept."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "s.jsonl"
        lines = []
        for n, item in enumerate(items):
            stamp = f"2099-01-01T00:00:{n:02d}Z"
            if item[0] == "user":
                msg = {"type": "user", "timestamp": stamp, "message": {"role": "user", "content": item[1]}}
            else:
                block = {"type": "tool_use", "id": f"t{n}", "name": item[1], "input": item[2]}
                msg = {"type": "assistant", "timestamp": stamp, "message": {"role": "assistant", "content": [block]}}
            lines.append(json.dumps(msg))
        path.write_text("\n".join(lines), encoding="utf-8")
        return mine.read_file(path, "f", "0", short=True)


RUN = ("tool", "mcp__playbook__playbook_run", {"name": "ship-it"})


class Corrections(unittest.TestCase):
    def test_next_message_refuses(self):
        turns = _session(("user", "ship the release"), RUN, ("user", "no, don't do that"))
        self.assertEqual(mine.corrections(turns), {"ship-it": 1})

    def test_revert_in_same_turn(self):
        turns = _session(("user", "ship the release"), RUN, ("tool", "Bash", {"command": "git restore x"}))
        self.assertEqual(mine.corrections(turns), {"ship-it": 1})

    def test_ordinary_conversation(self):
        turns = _session(("user", "ship the release"), ("tool", "Bash", {"command": "ls"}), ("user", "now add tests"))
        self.assertEqual(mine.corrections(turns), {})

    def test_agreement_is_not_a_correction(self):
        turns = _session(("user", "ship the release"), RUN, ("user", "nothing else, thanks"))
        self.assertEqual(mine.corrections(turns), {})


if __name__ == "__main__":
    unittest.main()
