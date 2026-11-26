import unittest

from helpers import mine, playbook  # noqa: F401

from getplaybook import store
from test_store import Home, make


class SharedSteps(Home):
    def test_two_playbooks_share_one_step(self):
        store.save(make("one", steps=["Run  the Script", "only in one"]))
        store.save(make("two", steps=["run the script", "run the script", "only in two"]))
        self.assertEqual(store.shared_steps(least=2), [("run the script", ["one", "two"])])

    def test_single_playbook_has_none(self):
        store.save(make("one", steps=["a step", "a step"]))
        self.assertEqual(store.shared_steps(least=2), [])


if __name__ == "__main__":
    unittest.main()
