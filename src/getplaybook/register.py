"""`playbook setup`: install the Claude Code plugin shipped inside this package, so one pip install and one command do it all.

A wheel cannot run code when it is installed, so this is a separate step. The plugin files (`plugin/`: the manifest, a
one-plugin marketplace, the hooks and the skill) are copied to `<home>/plugin`, a folder that outlives a reinstall or a
removed virtual environment, and Claude Code is told to add that marketplace and install the plugin from it.
"""
import shutil
import subprocess
from pathlib import Path

from . import config
from .build import find_claude

SHIPPED = Path(__file__).parent / "plugin"


def setup(dry_run=False, out=print) -> int:
    claude = find_claude()
    if not claude:
        out(f"Claude Code (the `claude` command) was not found, so only the library and the `{config.CLI}` command were installed. "
            f"Install Claude Code, then run `{config.CLI} setup` again.")
        return 0
    if not (SHIPPED / ".claude-plugin" / "plugin.json").is_file():
        out(f"The plugin files are missing from this install ({SHIPPED}); reinstall the getplaybook package.")
        return 1
    dest = config.home() / "plugin"
    commands = [[claude, "plugin", "marketplace", "add", str(dest)], [claude, "plugin", "install", "playbook@playbook"]]
    if dry_run:
        out(f"copy {SHIPPED} to {dest}")
        for cmd in commands:
            out(subprocess.list2cmdline(cmd))
        return 0
    shutil.copytree(SHIPPED, dest, dirs_exist_ok=True)
    out(f"Copied the plugin to {dest}")
    code = 0
    for cmd in commands:
        out("$ " + subprocess.list2cmdline(cmd))
        try:
            done = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", timeout=180)
            text, code = (done.stdout + done.stderr).strip(), done.returncode
        except (OSError, subprocess.SubprocessError) as why:
            text, code = str(why), 1
        out(text or f"exit code {code}")
        # a marketplace already added (an earlier setup, or this repo from GitHub) fails to add again: go on and install
    if code:
        out(f"The plugin was not installed. In Claude Code: /plugin marketplace add {dest}, then /plugin install playbook@playbook")
        return 1
    out("Done. The plugin's hooks and tools start in your next Claude Code session.")
    return 0
