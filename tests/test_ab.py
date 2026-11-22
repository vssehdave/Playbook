import importlib.util
import json
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("ab", Path(__file__).resolve().parent.parent / "scripts" / "ab.py")
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)


def hook(n, event, t, out=""):
    return [{"type": "system", "subtype": "hook_started", "hook_id": n, "hook_event": event, "_t": t},
            {"type": "system", "subtype": "hook_response", "hook_id": n, "hook_event": event, "_t": t + 0.2, "stdout": out}]


def note(event, name):
    text = '<system-reminder>\nPlaybook playbook "%s" matches' % name
    return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


def call(id_, tool, **given):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": id_, "name": tool, "input": given}]}}


def result(id_, text, error=False):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": id_, "content": text, "is_error": error}]}}


class ParseTest(unittest.TestCase):
    def test_offer_take_deny_recovery_and_agent_call(self):
        sent = {"subagent_type": "explorer", "description": "d", "prompt": "find it"}
        events = (hook("h1", "UserPromptSubmit", 0, note("UserPromptSubmit", "git-state"))
                  + [call("t1", "Bash", command="python -m unittest")]
                  + hook("h2", "PreToolUse", 1, '{"hookSpecificOutput": {"permissionDecision": "deny"}}')
                  + [result("t1", "refused", True), call("t2", "mcp__x__playbook_run", name="git-state"),
                     result("t2", "RESULT: pass\nAGENT CALL: " + json.dumps(sent)), call("t3", "Agent", **sent),
                     {"type": "result", "subtype": "success", "result": "done", "total_cost_usd": 0.01, "num_turns": 4,
                      "modelUsage": {"m": {"inputTokens": 5, "outputTokens": 7, "cacheReadInputTokens": 100, "cacheCreationInputTokens": 20}}}])
        got = ab.parse(events)
        self.assertEqual(got["offers"], [("UserPromptSubmit", "git-state")])
        self.assertEqual((got["denies"], got["recovered"], got["runs"], got["agent_unchanged"]), (1, True, ["git-state"], True))
        self.assertEqual((got["in"], got["out"], got["cache_read"], got["cache_write"], got["turns"], got["tools"]), (5, 7, 100, 20, 4, 3))
        self.assertEqual(got["hook_ms"], {"UserPromptSubmit": [200], "PreToolUse": [200]})
        self.assertEqual(ab.derived(got), {"offers": 1, "takes": 1, "repeats": 0})

    def test_rewritten_agent_call_is_flagged(self):
        sent = {"subagent_type": "explorer", "prompt": "find it"}
        events = [call("t2", "mcp__x__playbook_run", name="p"), result("t2", "RESULT: pass\nAGENT CALL: " + json.dumps(sent)),
                  call("t3", "Agent", subagent_type="explorer", prompt="find it, but my way")]
        self.assertIs(ab.parse(events)["agent_unchanged"], False)


class AggregateTest(unittest.TestCase):
    def test_real_needs_more_than_the_spread(self):
        self.assertTrue(ab.real([10, 11, 12], [20, 21, 22]))
        self.assertFalse(ab.real([10, 20, 30], [15, 25, 35]))
        self.assertFalse(ab.real([], [1]))
        self.assertFalse(ab.real([1], [9]))

    def test_table_marks_only_real_differences(self):
        def rec(arm, cost, run):
            return {"task": "a", "arm": arm, "run": run, "ok": True, "cost": cost, "in": 1, "out": 1, "cache_read": 0, "cache_write": 0, "turns": 3,
                    "tools": 2, "sub_tools": 0, "wall": 10.0, "offers_n": 0, "takes": 0}
        text = ab.table([rec("off", c, i) for i, c in enumerate((0.10, 0.11, 0.12))] + [rec("on", c, i) for i, c in enumerate((0.20, 0.21, 0.22))])
        on_line = next(line for line in text.splitlines() if line.startswith("on "))
        self.assertIn("0.210 [0.200-0.220]*", on_line)
        self.assertNotIn("3 [3-3]*", on_line)


if __name__ == "__main__":
    unittest.main()
