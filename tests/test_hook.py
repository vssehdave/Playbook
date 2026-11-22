"""The Claude Code hooks, run against a fixture session file."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import playbook
from test_delegate import delegating
from test_offer import ship
from test_store import Home

from getplaybook import hook, log

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

    def pre(self, command=None, **over):
        args = dict({"hook_event_name": "PreToolUse", "tool_use_id": "t3"}, **over)
        if command is not None:
            args["tool_input"] = {"command": command}
        return hook.run("pre-tool-use", json.dumps(self.payload(**args)))

    def test_enforce_refuses_a_step_of_the_offered_route_done_by_hand(self):
        self.transcript(*self.ladder())
        self.post()
        self.assertEqual(self.pre("git push"), "")  # suggest: offered, never refused
        playbook.approve("ship-change", mode="enforce")
        self.transcript(*self.ladder("Ship it now", "u2"))
        self.assertIn("set to enforce", self.post())
        out = json.loads(self.pre("git push origin main"))["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertTrue(out["permissionDecisionReason"].startswith('Playbook ship-change is set to enforce: call playbook_run {"name": "ship-change"'))
        for free in ({"command": "git status"}, {"command": "ls"}):
            self.assertEqual(self.pre(tool_input=free), "", free)
        self.assertEqual(self.pre(tool_name="Edit", tool_input={"file_path": "a.py"}), "")
        self.assertEqual(self.pre(tool_name="mcp__playbook__playbook_run", tool_input={"name": "ship-change"}), "")
        (Path(self.home) / "config.json").write_text(json.dumps({"playbooks": {"enforce": False}}), encoding="utf-8")
        self.assertEqual(self.pre("git push"), "")

    def test_enforce_stops_once_a_run_of_the_playbook_did_not_pass(self):
        playbook.approve("ship-change", mode="enforce")
        call = line("assistant", [{"type": "tool_use", "id": "r1", "name": "mcp__playbook__playbook_run", "input": {"name": "ship-change"}}], "m3b", mid="m3")

        def result(text):
            return line("user", [{"type": "tool_result", "tool_use_id": "r1", "content": [{"type": "text", "text": text}]}], "m3c")

        self.transcript(*self.ladder())
        self.post()
        self.transcript(*self.ladder(), call, result("RESULT: pass\n--- stdout ---\nok"))
        self.assertIn("deny", self.pre("git push"))
        self.transcript(*self.ladder(), call, result("RESULT: fail (exit 1)\nboom"))
        self.assertEqual(self.pre("git push"), "")
        self.transcript(*self.ladder(), *self.ladder("Next one please", "u9"))  # a new turn: nothing offered in it yet
        self.assertEqual(self.pre("git push"), "")

    def test_enforce_holds_for_a_playbook_offered_by_the_typed_words(self):
        playbook.approve("ship-change", mode="enforce")
        words = "committing and merging this change please"
        self.assertIn("set to enforce", hook.run("user-prompt-submit", json.dumps({"session_id": "s1", "prompt": words, "cwd": self.cwd})))
        self.transcript(typed(words))
        self.assertIn("deny", self.pre("git commit -m x"))


    def test_enforce_lets_a_delegate_playbook_send_its_returned_call_unchanged(self):
        saved = playbook.save(delegating())
        self.assertEqual(saved["route"], ["delegate"])
        playbook.approve("review-branch", mode="enforce")
        words = "please review branch main and look at the tests"
        self.assertIn("set to enforce", hook.run("user-prompt-submit", json.dumps({"session_id": "s1", "prompt": words, "cwd": self.cwd})))
        call = {"subagent_type": "crew:code-reviewer", "description": "x", "prompt": "Review main and the tests."}
        run = line("assistant", [{"type": "tool_use", "id": "r1", "name": "mcp__playbook__playbook_run", "input": {"name": "review-branch"}}], "m3b", mid="m3")
        done = line("user", [{"type": "tool_result", "tool_use_id": "r1", "content": [{"type": "text", "text": "RESULT: pass\nAGENT CALL: " + json.dumps(call)}]}], "m3c")

        def agent(**over):
            return self.pre(tool_name="Agent", tool_input=dict(call, **over))

        self.transcript(typed(words))
        self.assertIn("call playbook_run", agent())  # not run yet: the own call is refused
        self.transcript(typed(words), run, done)
        self.assertEqual(agent(), "")
        self.assertEqual(agent(description="other"), "")  # only the type and the prompt are compared
        for bad in (agent(prompt="Review main, quickly."), agent(subagent_type="explorer")):
            self.assertIn("send that AGENT CALL unchanged", bad)
        self.assertEqual(self.pre("git status"), "")
        failed = line("user", [{"type": "tool_result", "tool_use_id": "r1", "content": "RESULT: fail (exit 1)"}], "m3c")
        self.transcript(typed(words), run, failed)
        self.assertEqual(agent(prompt="my own"), "")  # a failed run frees the steps

    def test_the_offer_log_records_events_without_request_text(self):
        playbook.approve("ship-change", mode="enforce")
        words = "committing and merging this change please"
        hook.run("user-prompt-submit", json.dumps({"session_id": "s1", "prompt": words, "cwd": self.cwd}))
        self.transcript(typed(words))
        self.pre("git commit -m secret-message")
        run = {"tool_name": "mcp__playbook__playbook_run", "tool_input": {"name": "ship-change", "args": ["secret-arg"]}}
        self.pre(**run)
        self.post(**run, tool_response={"content": [{"type": "text", "text": "RESULT: pass\nok"}]})
        rows = [json.loads(x) for x in (Path(self.home) / "log.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["event"] for r in rows], ["offer", "deny", "take", "pass"])
        self.assertEqual({(r["playbook"], r["session_id"], r["agent_type"]) for r in rows}, {("ship-change", "s1", "")})
        self.assertEqual(rows[0]["mode"], "enforce")
        text = (Path(self.home) / "log.jsonl").read_text(encoding="utf-8")
        self.assertFalse(any(x in text for x in ("secret", "committing", "please")))
        self.assertEqual(log.stats(), [f"{'ship-change':<28} offers 1  takes 1  denies 1  passes 1  fails 0"])

    def test_enforce_works_under_a_host_from_the_transcript_alone(self):
        playbook.approve("ship-change", mode="enforce")
        with mock.patch.dict(os.environ, {"ALMANAC_PROXY": "1"}):
            self.transcript(*self.ladder("Ship it now", "u2"))  # route and narration agree: the host offered it
            self.assertEqual(self.post(), "")  # offers stay the host's
            out = json.loads(self.pre("git push origin main"))["hookSpecificOutput"]
            self.assertEqual(out["permissionDecision"], "deny")
            self.assertFalse((Path(self.home) / "sessions.json").exists())
            self.assertEqual(self.pre("git status"), "")
            self.transcript(*self.ladder("Ship it now", "u2")[:-3])  # only one signal before the call: not offered yet
            with mock.patch("getplaybook.offer._ahead", return_value=False):
                self.assertEqual(self.pre("git push origin main"), "")
            self.transcript(typed("committing and merging this change please"))  # offered by the typed words
            self.assertIn("deny", self.pre("git commit -m x"))
            self.transcript(*self.ladder("Ship it now", "u2"), *self.ladder("Next one please", "u9")[:1])  # a new turn
            self.assertEqual(self.pre("git push"), "")
            playbook.approve("ship-change")  # suggest: offered, never refused
            self.transcript(*self.ladder("Ship it now", "u2"))
            self.assertEqual(self.pre("git push"), "")


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

    def test_an_auto_playbook_is_run_by_the_hook_and_its_result_given(self):
        playbook.save({"name": "weekly-report", "title": "Weekly report", "when": "Send the weekly report.", "triggers": ["weekly report"],
                       "steps": ["Run the script"], "source": "owner"}, script="print('sent-42')\n")
        playbook.approve("weekly-report", mode="auto")
        text = json.loads(self.prompt("please send the weekly report for the team"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("has already run the script", text)
        self.assertIn("sent-42", text)
        (Path(self.home) / "config.json").write_text(json.dumps({"playbooks": {"auto_run": False}}), encoding="utf-8")
        self.assertIn("matches this request", self.prompt("please send the weekly report for the team"))


class SessionsLockTest(Home):
    def test_a_held_lock_skips_the_save_a_stale_one_is_taken_and_writers_do_not_lose_entries(self):
        lock = Path(self.home) / "sessions.lock"
        lock.write_text("", encoding="utf-8")
        with mock.patch.object(hook, "LOCK_WAIT", 0.1):
            hook._save("a:main", {"t": time.time()})
        self.assertEqual(hook._load(), {})
        os.utime(lock, (time.time() - 60, time.time() - 60))
        hook._save("a:main", {"t": time.time()})
        self.assertIn("a:main", hook._load())
        self.assertFalse(lock.exists())
        writers = [threading.Thread(target=hook._save, args=(f"s{i}:main", {"t": time.time()})) for i in range(8)]
        for w in writers:
            w.start()
        for w in writers:
            w.join()
        self.assertEqual(len(hook._load()), 9)


if __name__ == "__main__":
    unittest.main()
