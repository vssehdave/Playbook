"""Shared setup: a private PLAYBOOK_HOME before anything imports, and one namespace over the modules.

`playbook.save`, `playbook.find`, `playbook.run` and `mine.compile_top` read the way the tests were first written; each
name is looked up in the real modules, so nothing here can hide a missing function.
"""
import os
import tempfile
import warnings

warnings.simplefilter("ignore", ResourceWarning)
if "PLAYBOOK_HOME" not in os.environ:
    os.environ["PLAYBOOK_HOME"] = tempfile.mkdtemp(prefix="playbook-test-")
for _name in ("PLAYBOOK_CONFIG", "PLAYBOOK_CLAUDE", "ALMANAC_PROXY", "CLAUDE_CONFIG_DIR"):
    os.environ.pop(_name, None)  # the machine's own setup must not change what the tests see

from getplaybook import build, offer, run, store  # noqa: E402
from getplaybook import mine as _mine  # noqa: E402


class Namespace:
    def __init__(self, *modules):
        self._modules = modules

    def __getattr__(self, name):
        for module in self._modules:
            if hasattr(module, name):
                return getattr(module, name)
        raise AttributeError(name)


playbook = Namespace(store, run, offer)
mine = Namespace(_mine, build)
