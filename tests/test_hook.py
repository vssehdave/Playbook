"""The Claude Code hooks, run against a fixture session file."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import playbook
from test_offer import ship
from test_store import Home

from getplaybook import hook

ROOT = Path(__file__).parent.parent


def line(kind, content, uuid, **more):
    return json.dumps({"type": kind, "uuid": uuid, "sessionId": "s1", "message": {"role": kind, "id": more.pop("mid", uuid), "content": content}, **more})


def typed(text, uuid="u1"):
    return line("user", text, uuid)


def step(command, text, ident, uuid, **more):
    """What Claude Code writes for one assistant reply: a line for the narration, a line for the call, then the result."""
    out = []
    if text:
        out.append(line("assistant", [{"type": "text", "text": text}], uuid + "a", mid=uuid, **more))
    out.append(line("assistant", [{"type": "tool_use", "id": ident, "name": "Bash", "input": {"command": command}}], uuid + "b", mid=uuid, **more))
    out.append(line("user", [{"type": "tool_result", "tool_use_id": ident, "content": "ok"}], uuid + "c", **more))
    return out


class HookTest(Home):
    def setUp(self):
        super().setUp()
        self.dir = Path(tempfile.mkdtemp(prefix="playbook-hook-"))
        self.path = self.dir / "s1.jsonl"
        self.cwd = str(self.dir / "work" / "app")
        patch = mock.patch("getplaybook.offer._ahead", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)
        playbook.save(ship(), script="print(1)\n")
        playbook.approve("ship-change")

    def transcript(self, *lines):
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def payload(self, **over):
        return dict({"session_id": "s1", "transcript_path": str(self.path), "cwd": self.cwd, "hook_event_name": "PostToolUse",
                     "tool_name": "Bash", "tool_input": {}, "tool_use_id": "t2"}, **over)

    def ladder(self, prompt="Continue", uuid="u1"):
        return [typed(prompt, uuid), *step("python -m unittest", "Running the tests", "t1", "m1"), *step("git commit -m done", "Committing and merging", "t2", "m2")]

    def post(self, **over):
        return hook.run("post-tool-use", json.dumps(self.payload(**over)))

    def test_the_route_so_far_and_the_narration_make_an_offer(self):
        self.transcript(*self.ladder())
        out = json.loads(self.post())
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn('playbook "ship-change" covers the rest of this', text)
        self.assertTrue(text.startswith("<system-reminder>"))

    def test_the_call_the_hook_is_for_counts_before_the_session_file_has_it(self):
        with mock.patch("getplaybook.offer._ahead", return_value=False):
            self.transcript(*self.ladder()[:-3])  # the commit call is not in the file yet
            self.assertEqual(self.post(tool_input={"command": "git commit -m done"}), "")  # the route alone
            self.transcript(*self.ladder()[:-3], line("assistant", [{"type": "text", "text": "Committing and merging"}], "m2a", mid="m2"))
            self.assertIn("covers the rest of this", self.post(tool_input={"command": "git commit -m done"}))  # route and narration

    def test_once_per_playbook_per_turn_and_again_on_the_next_turn(self):
        self.transcript(*self.ladder())
        self.assertIn("covers the rest", self.post())
        self.assertEqual(self.post(), "")
        self.transcript(*self.ladder(), *self.ladder("Next, the docs please", "u9")[:1], *step("python -m unittest", "Running the tests", "t5", "m5"),
                        *step("git commit -m more", "Committing and merging", "t6", "m6"))
        self.assertIn("covers the rest", self.post(tool_use_id="t6"))

    def test_one_signal_alone_is_silent(self):
        self.transcript(typed("Continue"), *step("python -m unittest", "Ok", "t1", "m1"), *step("git commit -m x", "Ok", "t2", "m2"))
        with mock.patch("getplaybook.offer._ahead", return_value=False):
            self.assertEqual(self.post(), "")

    def test_a_playbook_already_run_is_not_offered_again(self):
        run_call = line("assistant", [{"type": "tool_use", "id": "t0", "name": "mcp__plugin_playbook_playbook__playbook_run", "input": {"name": "ship-change"}}], "m0b", mid="m0")
        self.transcript(typed("Continue"), run_call, *self.ladder()[1:])
        self.assertEqual(self.post(), "")

    def test_a_host_that_places_offers_itself_silences_the_hooks(self):
        self.transcript(*self.ladder())
        for value in ("1", "http://127.0.0.1:8080"):
            with mock.patch.dict(os.environ, {"ALMANAC_PROXY": value}):
                self.assertEqual(self.post(), "")
                self.assertEqual(hook.run("user-prompt-submit", json.dumps({"prompt": "please send the weekly report", "cwd": self.cwd})), "")
        self.assertFalse((Path(self.home) / "sessions.json").exists())

    def test_nothing_raises_on_bad_input(self):
        for raw in ("", "not json", "[1]", json.dumps({"transcript_path": 5}), json.dumps(self.payload(transcript_path=str(self.dir / "none.jsonl")))):
            self.assertEqual(hook.run("post-tool-use", raw), "")
        self.assertEqual(hook.run("no-such-event", "{}"), "")

    def test_off_when_route_offers_are_off(self):
        self.transcript(*self.ladder())
        (Path(self.home) / "config.json").write_text(json.dumps({"playbooks": {"route": False}}), encoding="utf-8")
        self.assertEqual(self.post(), "")

    def test_a_sub_agent_is_judged_from_its_own_file_and_type(self):
        playbook.save(ship(), script="print(1)\n")
        playbook.save({**ship(), "name": "verify", "triggers": ["running the tests"], "route": ["test"], "agent_type": "verifier"}, script="print(2)\n")
        playbook.approve("verify")
        sub = self.dir / "s1" / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-7.jsonl").write_text("\n".join(line(k, c, u, isSidechain=True) for k, c, u in (
            ("user", "Check the billing change", "x1"), ("assistant", [{"type": "text", "text": "Running the tests"}], "x2"),
            ("assistant", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "python -m unittest"}}], "x3"))) + "\n", encoding="utf-8")
        self.transcript(*self.ladder())
        self.assertIn('playbook "verify"', json.loads(self.post(agent_id="7", agent_type="verifier", tool_use_id="t1"))["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.post(agent_id="7", agent_type="other", tool_use_id="t1"), "")

    def test_the_command_prints_hook_json_and_exits_zero(self):
        self.transcript(*self.ladder())
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PLAYBOOK_HOME=self.home)
        env.pop("ALMANAC_PROXY", None)
        # the folder does not exist, so the repo signal is off: the route and the narration are the two that count
        done = subprocess.run([sys.executable, "-m", "getplaybook", "hook", "post-tool-use"], input=json.dumps(self.payload()).encode(),
                              capture_output=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("covers the rest of this", json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"])
        broken = subprocess.run([sys.executable, "-m", "getplaybook", "hook", "post-tool-use"], input=b"\xff\xfe nope", capture_output=True, env=env, timeout=60)
        self.assertEqual((broken.returncode, broken.stdout), (0, b""))


class WordsHookTest(Home):
    def prompt(self, text, **over):
        return hook.run("user-prompt-submit", json.dumps(dict({"session_id": "s1", "prompt": text, "cwd": "/tmp/app"}, **over)))

    def setUp(self):
        super().setUp()
        playbook.save({"name": "weekly-report", "title": "Weekly report", "when": "Send the weekly report.",
                       "triggers": ["weekly report"], "steps": ["Run the script"], "source": "owner"})

    def test_a_matching_request_gets_the_note_and_counts_a_use(self):
        out = json.loads(self.prompt("please send the weekly report for the team"))["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "UserPromptSubmit")
        self.assertIn('playbook "weekly-report" matches this request', out["additionalContext"])
        self.assertEqual(playbook.load("weekly-report")["uses"], 1)

    def test_short_follow_up_or_other_words_say_nothing(self):
        for text in ("ok", "keep going", "something quite unrelated here", ""):
            self.assertEqual(self.prompt(text), "", text)
        self.assertEqual(self.prompt(5), "")

    def test_a_playbook_offered_by_its_words_is_not_offered_again_by_route_in_that_turn(self):
        self.prompt("please send the weekly report for the team")
        state = json.loads((Path(self.home) / "sessions.json").read_text(encoding="utf-8"))
        self.assertEqual(state["s1:main"]["word"], "weekly-report")


if __name__ == "__main__":
    unittest.main()
