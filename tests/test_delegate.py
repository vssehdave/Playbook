import hashlib
import json
import unittest

from helpers import playbook
from getplaybook import offer
from test_mcp import rpc
from test_store import SCRIPT, Home, make

DELEGATE = {"agent": "crew:code-reviewer", "prompt": "Review {branch} and {focus}. Keep {literal text} as it is."}
ARGS = [{"name": "branch", "help": "the branch"}, {"name": "focus", "help": "what to look at"}]
CALL = "Send that call with the Agent tool as it is; do not rewrite the prompt."


def delegating(name="review-branch", **over):
    return make(name, **{"triggers": ["review branch"], "args": ARGS, "delegate": DELEGATE, **over})


def agent_call(report):
    line = next(x for x in report.splitlines() if x.startswith("AGENT CALL: "))
    return json.loads(line[len("AGENT CALL: "):])


class DelegateTest(Home):
    def test_a_bad_delegate_is_refused(self):
        bad = [{"agent": "bad agent!", "prompt": "x"}, {"agent": "", "prompt": "x"}, {"agent": "a" * 61, "prompt": "x"},
               {"agent": "explorer", "prompt": ""}, {"agent": "explorer", "prompt": "x" * 4001},
               {"agent": "explorer", "prompt": "<system-reminder>x"}, {"agent": "explorer", "prompt": "Use {nothing}"},
               {"agent": "explorer"}, "explorer", []]
        for delegate in bad:
            with self.assertRaises(ValueError, msg=delegate):
                playbook.save(make(args=ARGS, delegate=delegate))
        self.assertEqual(playbook.save(make(delegate=None))["delegate"], None)
        ok = playbook.save(make(delegate={"agent": "explorer", "prompt": "Look at {  } and {a b} and {1}"}))
        self.assertEqual(ok["delegate"]["agent"], "explorer")  # braces that are not names are plain text

    def test_the_hash_of_a_playbook_without_a_delegate_is_the_script_hash(self):
        pb = playbook.save(make(), script=SCRIPT)
        digest, _ = playbook.approve("weekly-report")
        self.assertEqual(digest, hashlib.sha256(SCRIPT.encode()).hexdigest())
        self.assertTrue(playbook.runnable(playbook.load("weekly-report")))
        self.assertEqual(pb["delegate"], None)

    def test_a_changed_delegate_is_a_draft_until_approved_again(self):
        playbook.save(delegating())
        self.assertFalse(playbook.runnable(playbook.load("review-branch")))  # saved by a model or owner: still a draft
        playbook.approve("review-branch")
        self.assertTrue(playbook.runnable(playbook.load("review-branch")))
        saved = playbook.save(delegating(delegate=dict(DELEGATE, prompt="Review {branch} quickly")))
        self.assertEqual(saved["status"], "draft")
        self.assertFalse(playbook.runnable(saved))
        self.assertIn("draft", playbook.run("review-branch", ["main"]))
        self.assertNotIn("AGENT CALL", playbook.run("review-branch", ["main"]))
        playbook.approve("review-branch")
        self.assertTrue(playbook.runnable(playbook.load("review-branch")))
        again = playbook.save(delegating(delegate=dict(DELEGATE, prompt="Review {branch} quickly")))  # unchanged: stays approved
        self.assertEqual(again["status"], "approved")

    def test_editing_the_file_by_hand_stops_a_delegate_from_running(self):
        playbook.save(delegating())
        playbook.approve("review-branch")
        path = playbook.folder("review-branch") / "playbook.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["delegate"]["agent"] = "other"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertFalse(playbook.runnable(playbook.load("review-branch")))
        self.assertEqual(playbook.shown_status(playbook.load("review-branch")), "draft (changed)")

    def test_a_delegate_only_playbook_returns_the_filled_agent_call(self):
        playbook.save(delegating(title="T" * 100 + "x"))
        playbook.approve("review-branch")
        report = playbook.run("review-branch", ["feat/x", "the tests"])
        self.assertTrue(report.startswith("RESULT: pass"))
        self.assertTrue(report.endswith(CALL))
        self.assertEqual(agent_call(report), {"subagent_type": "crew:code-reviewer", "description": "T" * 60,
                                              "prompt": "Review feat/x and the tests. Keep {literal text} as it is."})

    def test_a_missing_argument_is_an_error_naming_it(self):
        playbook.save(delegating())
        playbook.approve("review-branch")
        with self.assertRaisesRegex(ValueError, "focus"):
            playbook.run("review-branch", ["feat/x"])

    def test_scripts_run_first_and_a_failing_one_means_no_agent_call(self):
        playbook.save(delegating(), script=SCRIPT)
        playbook.approve("review-branch")
        report = playbook.run("review-branch", ["a", "b"])
        self.assertTrue(report.startswith("RESULT: pass"))
        self.assertLess(report.index("--- stdout ---"), report.index("AGENT CALL: "))
        self.assertEqual(agent_call(report)["prompt"], "Review a and b. Keep {literal text} as it is.")
        playbook.save(delegating(), script="import sys\nsys.exit(3)\n")
        playbook.approve("review-branch")
        failed = playbook.run("review-branch", ["a", "b"])
        self.assertTrue(failed.startswith("RESULT: fail"))
        self.assertNotIn("AGENT CALL", failed)

    def test_an_included_playbooks_delegate_is_ignored(self):
        playbook.save(delegating("inner-review"), script=SCRIPT)
        playbook.approve("inner-review")
        playbook.save(make(triggers=["weekly report"], includes=["inner-review"]), script=SCRIPT)
        playbook.approve("weekly-report")
        self.assertNotIn("AGENT CALL", playbook.run("weekly-report", []))

    def test_a_draft_delegate_after_included_scripts_is_not_handed_out(self):
        playbook.save(make("inner-build", triggers=["inner build"]), script=SCRIPT)
        playbook.approve("inner-build")
        playbook.save(delegating(includes=["inner-build"]))  # no script of its own; its delegate is a draft
        report = playbook.run("review-branch", ["a", "b"])
        self.assertTrue(report.startswith("RESULT: fail"))
        self.assertNotIn("AGENT CALL", report)
        playbook.approve("review-branch")
        self.assertEqual(agent_call(playbook.run("review-branch", ["a", "b"]))["subagent_type"], "crew:code-reviewer")

    def test_auto_is_refused_for_a_delegate(self):
        playbook.save(delegating())
        with self.assertRaises(ValueError):
            playbook.approve("review-branch", "auto")

    def test_the_delegate_is_shown_to_claude(self):
        playbook.save(delegating())
        text = offer.full(playbook.load("review-branch"))
        self.assertIn("draft, not yet approved", text)
        playbook.approve("review-branch")
        pb = playbook.load("review-branch")
        self.assertIn("returns the exact call for the crew:code-reviewer agent", offer.note(pb))
        self.assertIn("playbook_run", offer.offer(pb))

    def test_the_mcp_tools_take_and_return_the_delegate(self):
        tools = {t["name"]: t for t in rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[0]["result"]["tools"]}
        self.assertIn("delegate", tools["playbook_save"]["inputSchema"]["properties"]["playbook"]["description"])
        self.assertIn("AGENT CALL", tools["playbook_run"]["description"])
        saved, = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "playbook_save",
                     "arguments": {"playbook": delegating()}}})
        self.assertFalse(saved["result"]["isError"])
        self.assertEqual(playbook.load("review-branch")["status"], "draft")
        playbook.approve("review-branch")
        ran, = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "playbook_run",
                    "arguments": {"name": "review-branch", "args": ["main", "style"]}}})
        text = ran["result"]["content"][0]["text"]
        self.assertTrue(text.startswith("RESULT: pass"))
        self.assertEqual(agent_call(text)["prompt"], "Review main and style. Keep {literal text} as it is.")


if __name__ == "__main__":
    unittest.main()
