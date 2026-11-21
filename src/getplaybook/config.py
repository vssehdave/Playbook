"""Where Playbook keeps its files, its settings, and which Claude Code folders it may read.

Home is `$PLAYBOOK_HOME`, else `~/.playbook`: `playbooks/` (one folder each), `auto.json` and `auto.key` (the owner's
signed approvals for the enforce and auto modes), `sessions.json` (what the hooks already offered) and `config.json`.

`config.json` holds one section, `playbooks`; every key is optional and the defaults below apply to the rest:
    {"playbooks": {"route": false, "transcript_dirs": ["~/.claude-work"]}}

A program that embeds Playbook can keep its own home and settings: set `home_fn` (returns the folder) and `load_fn`
(returns its whole config, with the same `playbooks` section), and change `NAME` and `CLI`, the words reminders use.
"""
import json
import os
from pathlib import Path

NAME = "Playbook"  # how reminders name the tool
CLI = "playbook"  # the command the texts tell the person to run
SPAWN = None  # the command that starts the background worker, when a host wants its own
home_fn = None
load_fn = None

DEFAULTS = {
    "enabled": True,
    "inject": True,  # a playbook whose triggers match the typed request is offered
    "route": True,  # a playbook is offered from what Claude is doing (its tool calls and narration)
    "auto_run": True,  # a script approved with `--mode auto` is run before the model sees a matching request; false never runs one
    "enforce": True,  # a playbook approved with `--mode enforce` refuses the hand-run steps it covers; false turns that off
    "transcript_dirs": [],  # Claude Code config folders to mine besides ~/.claude and $CLAUDE_CONFIG_DIR
    # a session folder whose name holds one of these is not mined (scratch and temporary work)
    "skip_folders": ["AppData-Local-Temp", "-Temp-", "playbook-compile", "-tmp-", "-private-var-folders-"],
    "background": True,  # mine past sessions and compile the best repeat into a draft, in the background
    "compile_per_day": 1,  # most Claude Code sessions a day that do the compiling; 0 turns it off
}


def home() -> Path:
    if home_fn:
        return Path(home_fn())
    path = Path(os.environ.get("PLAYBOOK_HOME") or Path.home() / ".playbook")
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return Path(os.environ.get("PLAYBOOK_CONFIG") or home() / "config.json")


def load() -> dict:
    """The whole config as a dict with a `playbooks` section; never raises."""
    if load_fn:
        return load_fn()
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def settings(cfg=None) -> dict:
    """The `playbooks` settings: the defaults, then what the config says."""
    given = (load() if cfg is None else cfg).get("playbooks")
    return {**DEFAULTS, **(given if isinstance(given, dict) else {})}


def transcript_dirs(cfg=None) -> list:
    """Claude Code config folders with a `projects/` folder of session files: ~/.claude, $CLAUDE_CONFIG_DIR and the
    `transcript_dirs` setting, those that exist. Playbook only reads them."""
    env = os.environ.get("CLAUDE_CONFIG_DIR") or ""
    extra = settings(cfg)["transcript_dirs"]
    found, seen = [], set()
    for raw in [Path.home() / ".claude", env, *(extra if isinstance(extra, list) else [])]:
        if not isinstance(raw, (str, Path)) or not str(raw):
            continue
        path = Path(raw).expanduser()
        real = os.path.normcase(os.path.realpath(path))
        if real not in seen and (path / "projects").is_dir():
            seen.add(real)
            found.append(path)
    return found


def hosted() -> bool:
    """True when a program that places offers itself launched this Claude Code (it says so with ALMANAC_PROXY), so the hooks stay silent."""
    return os.environ.get("ALMANAC_PROXY", "") not in ("", "0")
