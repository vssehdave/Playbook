"""Route offers: two of three signals (route, narration, repo), decided from the assistant messages of a turn."""
import unittest
from unittest import mock

from helpers import playbook
from test_store import Home, make

SHIP = ["test", "commit", "push", "pr", "ci", "merge"]


def use(command, text="", ident="t1", name="Bash", args=None):
    """An assistant message: optional narration, then one tool call."""
    content = [{"type": "text", "text": text}] if text else []
    content.append({"type": "tool_use", "id": ident, "name": name, "input": args if args is not None else {"command": command}})
    return {"role": "assistant", "content": content}


def ship(**over):
    fields = dict(title="Ship a change", triggers=["committing and merging"], route=SHIP, skills=[], agents=[],
                  args=[{"name": "--message", "help": "commit message"}])
    return make("ship-change", **{**fields, **over})


LADDER = [use("python -m unittest", "Running the tests", "t1"), use("git commit -m done", "Committing and merging", "t2")]


class DecideTest(Home):
    def setUp(self):
        super().setUp()
        patch = mock.patch("getplaybook.offer._ahead", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)

    def approved(self, pb=None, script="print(1)\n"):
        playbook.save(pb or ship(), script=script)
        playbook.approve((pb or ship())["name"])

    def test_route_narration_and_repo_offer_the_playbook(self):
        self.approved()
        found = playbook.decide(LADDER)
        self.assertEqual((found["name"], found["signals"]), ("ship-change", ["route", "narration", "repo"]))
        self.assertIn('playbook "ship-change" covers the rest of this. Run playbook_run {"name": "ship-change", "args": [<--message>]} for this instead of doing it by hand; ', found["text"])
        self.assertIn("--message: commit message", found["text"])
        self.assertTrue(found["text"].startswith("<system-reminder>") and found["text"].endswith("</system-reminder>"))

    def test_one_signal_alone_is_not_an_offer(self):
        self.approved()
        with mock.patch("getplaybook.offer._ahead", return_value=False):
            self.assertIsNone(playbook.decide([use("python -m unittest", "Ok", "t1"), use("git commit -m x", "Ok", "t2")]))  # the route alone
            self.assertIsNone(playbook.decide([use("ls", "Committing and merging", "t1")]))  # the narration alone
        self.assertIsNone(playbook.decide([use("git status", "Just looking", "t1")]))  # the repo alone (it says nothing without another signal)

    def test_the_repo_is_asked_once_and_only_for_a_route_that_ships(self):
        self.approved()
        calls = []
        playbook.decide(LADDER, ahead=lambda: calls.append(1) or True)
        self.assertEqual(calls, [1])
        playbook.save(make("check-it", triggers=["running the tests"], route=["test"], skills=[], agents=[]), script="print(2)\n")
        playbook.approve("check-it")
        calls.clear()
        playbook.decide([use("python -m unittest", "Running the tests")], ahead=lambda: calls.append(1) or True, offered=["ship-change"])
        self.assertEqual(calls, [])

    def test_a_route_written_as_a_set_of_phases_matches_in_any_order(self):
        playbook.save(make("ship-old", triggers=["committing and merging"], phases=["branch", "commit", "edit", "pr", "push", "test"], skills=[], agents=[]))
        playbook.approve("ship-old")  # the miner stored the phases sorted
        self.assertEqual(playbook.decide(LADDER)["name"], "ship-old")

    def test_a_sub_agent_playbook_is_offered_only_to_its_type_and_never_to_main(self):
        for name, kind in (("verify", "verifier"), ("main-verify", "")):
            playbook.save(make(name, title="Run the tests", triggers=["running the tests"], route=["test"], agent_type=kind, skills=[], agents=[]))
            playbook.approve(name)
        asked = [use("python -m unittest", "Running the tests")]
        self.assertEqual(playbook.decide(asked, kind="verifier")["name"], "verify")
        self.assertIsNone(playbook.decide(asked, kind="other"))
        self.assertEqual(playbook.decide(asked)["name"], "main-verify")

    def test_an_inner_playbook_is_offered_on_its_own_and_not_after_its_outer_was_taken(self):
        playbook.save(make("verify", title="Run the tests", triggers=["running the tests"], route=["test"], skills=[], agents=[]), script="print(1)\n")
        playbook.save(ship(includes=["verify"]), script="print(2)\n")
        playbook.approve("verify")
        playbook.approve("ship-change")
        asked = [use("python -m unittest", "Running the tests")]
        self.assertEqual(playbook.decide(asked)["name"], "verify")
        self.assertIsNone(playbook.decide(asked, called=["ship-change"]))
        self.assertIsNone(playbook.decide(asked, offered=["verify"]))

    def test_a_draft_or_a_playbook_of_another_project_is_not_offered(self):
        playbook.save(ship(source="claude"))  # a draft
        self.assertIsNone(playbook.decide(LADDER))
        playbook.save(ship(project="elsewhere/app", source="owner"))
        self.assertIsNone(playbook.decide(LADDER, project=["here/app", "app"]))
        self.assertEqual(playbook.decide(LADDER, project=["elsewhere/app", "app"])["name"], "ship-change")

    def test_the_offer_for_a_playbook_without_a_script_says_to_read_its_steps(self):
        playbook.save(ship(source="owner", args=[]))
        self.assertIn('Read its steps with playbook_find {"query": "ship-change"}', playbook.decide(LADDER)["text"])


class WordsTest(Home):
    def test_note_names_the_tool_the_steps_and_stays_in_its_frame(self):
        playbook.save(make(), script="print(1)\n")
        playbook.approve("weekly-report")
        note = playbook.note(playbook.load("weekly-report"))
        self.assertTrue(note.startswith('<system-reminder>\nPlaybook playbook "weekly-report" matches this request'))
        self.assertTrue(note.endswith("</system-reminder>"))
        self.assertLessEqual(len(note), playbook.NOTE_LIMIT)
        self.assertIn('Run playbook_run {"name": "weekly-report", "args": []} for this instead of doing it by hand.', note)


if __name__ == "__main__":
    unittest.main()
