import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from helpers import playbook
from test_store import Home, make

from getplaybook import cli, steps

class ResultTest(Home):
    def approved(self, name, script, **over):
        playbook.save(make(name, **over), script=script)
        playbook.approve(name)

    def test_pass_and_fail_lead_with_one_result_line(self):
        self.approved("weekly-report", "print('all good')\n")
        out = playbook.run("weekly-report", [])
        self.assertEqual(out.split("\n")[0], "RESULT: pass")
        self.assertIn("all good", out)
        failing = ("import sys\nsys.stderr.write('noise\\n' + '=' * 70 + '\\nFAIL: test_a (m.T)\\n' + '-' * 70 + '\\nTraceback (most recent call last):\\n"
                   "  File x\\nAssertionError: boom\\n\\n' + '-' * 70 + '\\nRan 3 tests in 0.1s\\n\\nFAILED (failures=1)\\n')\nsys.exit(1)\n")
        self.approved("weekly-report", failing)
        out = playbook.run("weekly-report", [])
        self.assertEqual(out.split("\n")[0], "RESULT: fail (exit 1)")
        self.assertIn("Failing: test_a (m.T)", out)
        self.assertIn("AssertionError: boom", out)
        self.assertNotIn("noise", out)
        self.assertNotIn("Ran 3 tests", out)
        self.assertNotIn("RESUME", out)  # one step: nothing to go on from

    def test_a_failure_with_no_test_output_shows_the_end_of_it_and_a_timeout_is_a_failure(self):
        self.approved("weekly-report", "import sys\nprint('\\n'.join(str(i) for i in range(100)))\nsys.exit(2)\n")
        out = playbook.run("weekly-report", [])
        self.assertTrue(out.startswith("RESULT: fail (exit 2)\n"))
        self.assertIn("99", out)
        self.assertNotIn("\n10\n", out)
        self.approved("weekly-report", "import time\ntime.sleep(30)\n")
        self.assertTrue(playbook.run("weekly-report", [], timeout=1).startswith("RESULT: fail (timed out after 1 seconds"))


    def test_a_failure_report_printed_on_stdout_is_kept_and_a_pass_keeps_its_last_lines(self):
        self.approved("weekly-report", "import json, sys\nprint(json.dumps({'ok': False, 'failed': 'git push'}))\nsys.exit(1)\n")
        out = playbook.run("weekly-report", [])
        self.assertIn('{"ok": false, "failed": "git push"}', out)
        self.approved("weekly-report", "print('x' * 5000)\nprint('https://example.test/pull/7')\n")
        self.assertIn("https://example.test/pull/7", playbook.run("weekly-report", []))


class ComposeTest(Home):
    def setUp(self):
        super().setUp()
        self.cwd = tempfile.mkdtemp(prefix="playbook-comp-")
        count = "open('count.txt', 'a').write('x')\n"
        for name, script, over in (
                ("verify", count + "print('verified')\n", {}),
                ("wait-ci", "import os, sys\nif not os.path.exists('green'):\n    print('ci is red', file=sys.stderr)\n    sys.exit(1)\nprint('ci green')\n", {}),
                ("ship-change", "print('shipped')\n", {"includes": ["verify", "wait-ci"]})):
            playbook.save(make(name, **over), script=script)
            playbook.approve(name)

    def test_inner_playbooks_run_in_order_and_stop_at_the_first_failure(self):
        out = playbook.run("ship-change", [], self.cwd)
        lines = out.split("\n")
        self.assertEqual(lines[:2], ["RESULT: fail (exit 1)", "FAILED STEP: wait-ci (2 of 3)"])
        self.assertIn("ci is red", out)
        self.assertEqual(json.loads(lines[-1].split("RESUME: playbook_run ")[1]), {"name": "ship-change", "from": "wait-ci"})
        self.assertNotIn("shipped", out)
        Path(self.cwd, "green").write_text("")
        out = playbook.run("ship-change", [], self.cwd)
        self.assertTrue(out.startswith("RESULT: pass\nSteps run: verify, wait-ci, ship-change\n"))
        self.assertIn("shipped", out)

    def test_resume_goes_on_from_the_step_and_does_not_run_the_ones_before(self):
        playbook.run("ship-change", [], self.cwd)  # fails at wait-ci after verify ran once
        Path(self.cwd, "green").write_text("")
        for start in ("wait-ci", "2"):
            out = playbook.run("ship-change", [], self.cwd, start=start)
            self.assertTrue(out.startswith("RESULT: pass\nSteps run: wait-ci, ship-change\n"), out)
        self.assertEqual(Path(self.cwd, "count.txt").read_text(), "x")  # verify ran once in all
        with self.assertRaises(ValueError):
            playbook.run("ship-change", [], self.cwd, start="nope-nope")
        with self.assertRaises(ValueError):
            playbook.run("ship-change", [], self.cwd, start="9")

    def test_the_outer_arguments_go_to_its_own_script_and_the_resume_line_keeps_them(self):
        playbook.save(make("ship-change", includes=["wait-ci"], args=[{"name": "--msg", "help": "m"}]),
                      script="import sys\nprint(sys.argv[1:])\n")
        playbook.approve("ship-change")
        out = playbook.run("ship-change", ["--msg", "x"], self.cwd)
        self.assertEqual(json.loads(out.split("RESUME: playbook_run ")[1]), {"name": "ship-change", "from": "wait-ci", "args": ["--msg", "x"]})
        Path(self.cwd, "green").write_text("")
        self.assertIn("['--msg', 'x']", playbook.run("ship-change", ["--msg", "x"], self.cwd, start="ship-change"))

    def test_loops_missing_inner_playbooks_and_an_unapproved_inner_one_are_refused_or_reported(self):
        with self.assertRaises(ValueError):
            playbook.save(make("self-loop", includes=["self-loop"]))
        playbook.save(make("a-one", includes=["b-one"]), script="print(1)\n")
        playbook.save(make("b-one", includes=["a-one"]), script="print(1)\n")
        with self.assertRaises(ValueError):
            playbook.run("a-one", [])
        playbook.save(make("c-one", includes=["gone-one"]))
        with self.assertRaises(ValueError):
            playbook.run("c-one", [])
        playbook.save(make("draft-inner"), script="print(1)\n")  # a draft script
        playbook.save(make("d-one", includes=["draft-inner"]))
        self.assertTrue(playbook.run("d-one", []).startswith("RESULT: fail (not run)"))


class DedupeTest(Home):
    def save(self, name, triggers, **over):
        return playbook.save(make(name, **dict(dict(triggers=triggers, route=["commit", "push", "pr"], skills=[], agents=[]), **over)))

    def test_near_identical_drafts_merge_and_approved_ones_are_left_alone(self):
        self.save("ship-a", ["ship a change"], source="claude")
        self.save("ship-b", ["push a pr"], source="miner")
        self.save("ship-c", ["merge the pr"])  # the person's: approved
        self.save("other-way", ["clean up"], route=["test", "install"], source="claude")
        lines = []
        self.assertEqual(playbook.dedupe(out=lines.append), 1)
        self.assertEqual(lines, ["merged ship-b into ship-a"])
        self.assertEqual(playbook.names(), ["other-way", "ship-a", "ship-c"])
        self.assertEqual(playbook.load("ship-a")["triggers"], ["ship a change", "push a pr"])
        self.assertEqual(playbook.load("ship-c")["triggers"], ["merge the pr"])
        self.assertEqual(playbook.dedupe(include_approved=True, out=lines.append), 1)
        self.assertEqual(playbook.names(), ["other-way", "ship-c"])  # the approved one is the one kept
        self.assertEqual(playbook.load("ship-c")["triggers"], ["merge the pr", "ship a change", "push a pr"])
        self.assertEqual(playbook.dedupe(), 0)

    def test_a_different_agent_type_or_project_is_a_different_playbook(self):
        self.save("ship-a", ["ship a change"], source="claude")
        self.save("ship-b", ["push a pr"], source="claude", agent_type="shipper")
        self.save("ship-c", ["merge the pr"], source="claude", project="elsewhere")
        self.assertEqual(playbook.dedupe(), 0)

    SHIP_STEPS = ["If not on a feature branch: git checkout -b <name>", "Edit with Write or Edit", "git add -- <files>, then git commit -m ...",
                  "git push -u origin <branch>", "gh pr create --title ..."]

    def test_a_draft_with_no_route_is_read_from_its_steps(self):
        self.save("ship-routed", ["ship a change"], source="claude", route=["branch", "edit", "commit", "push", "pr"])
        self.save("ship-prose", ["open a pr"], source="claude", route=[], steps=self.SHIP_STEPS)
        self.save("ship-other-agent", ["push work"], source="claude", route=[], steps=self.SHIP_STEPS, agent_type="shipper")
        self.save("test-prose", ["run tests"], source="claude", route=[], steps=["python -m unittest", "pip install -e ."])
        lines = []
        self.assertEqual(playbook.dedupe(dry_run=True, out=lines.append), 1)
        self.assertEqual(lines, ["would merge ship-routed into ship-prose"])
        self.assertEqual(len(playbook.names()), 4)  # a dry run changes nothing
        lines.clear()
        self.assertEqual(playbook.dedupe(out=lines.append), 1)
        self.assertEqual(lines, ["merged ship-routed into ship-prose"])
        self.assertEqual(playbook.names(), ["ship-other-agent", "ship-prose", "test-prose"])  # another agent type stays apart

    def test_the_prose_phases_count_a_pr_check_as_ci_only(self):
        self.assertEqual(steps.prose_phases(["watch gh pr checks --watch, then gh pr merge"]), {"ci", "merge"})

    def test_one_that_others_include_keeps_its_name_and_the_cli_prints_what_it_merged(self):
        self.save("ship-a", ["ship a change"], source="claude")
        self.save("ship-b", ["push a pr"], source="claude")
        playbook.save(make("outer-one", includes=["ship-b"], source="claude", route=["test", "install"], skills=[], agents=[]))
        self.assertEqual(playbook.dedupe(), 0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["dedupe"]), 0)
        self.assertEqual(out.getvalue().strip(), "nothing to merge")


if __name__ == "__main__":
    unittest.main()
