"""What a tool call did: the commands of a Bash call, and the phase of work each belongs to (test, commit, push ...)."""
import re

from .text import redact

_CD = re.compile(r"^\s*cd\s+(\"[^\"]*\"|\S+)\s*(&&|;)\s*")
MAX_COMMAND = 8192  # a longer command is cut here, before it is read
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
_WORD = re.compile(r"^(-{1,2}[A-Za-z][A-Za-z-]*|[a-z][a-z0-9_.-]*)$")  # all a step keeps after the program name
PYTHONS = frozenset(("python", "python3", "py"))
THREE = PYTHONS | {"gh", "npm", "uv"}  # programs whose third word matters: gh pr merge, python -m build, npm run test
PYFLAGS = frozenset("-B -u -E -I -s -S -O -OO".split())
_PIECES = re.compile(r"&&|\|\||[;|\n]")
_COMMAND = re.compile(r"^[A-Za-z][\w.-]*$")
NOISE = frozenset("cd echo printf export set sleep wait true exit do done then else fi for while if".split())
# Read-only filters say nothing about the way of working: left out of the steps (not the tools). `sed -n` too, `sed -i` not.
FILTERS = frozenset("head tail cut cat wc sort uniq grep ls tr tee xargs".split())
# The phase of work a tool call belongs to; the first pattern that matches wins.
PHASES = [(name, re.compile(pattern)) for name, pattern in (
    ("release", r"^Bash: (git tag|gh release|twine|npm publish|(python3?|py) -m build)\b"),
    ("ci", r"^Bash: (gh run|gh pr checks)\b"),
    ("merge", r"^Bash: (gh pr merge|git merge)\b"),
    ("pr", r"^Bash: gh pr\b"),
    ("push", r"^Bash: git push\b"),
    ("commit", r"^Bash: git (add|commit)\b"),
    ("branch", r"^Bash: git (checkout|switch|worktree|branch)\b"),
    ("sync", r"^Bash: git (pull|fetch|rebase)\b"),
    ("test", r"^Bash: (pytest|(python3?|py) -m (pytest|unittest)|npm test|npm run|npx|make test|yarn test|pnpm test|cargo test|go test|uv run)\b"),
    ("install", r"^Bash: (pip|pipx|uv pip|npm i(nstall)?|claude plugin|(python3?|py) -m pip)\b"),
    ("delegate", r"^Agent: "),
    ("skill", r"^Skill: "),
    ("web", r"^(WebSearch|WebFetch)$"),
    ("browser", r"(?i)playwright|browser|chrome"),
    ("schedule", r"^(RemoteTrigger|CronCreate|ScheduleWakeup)$"),
    ("edit", r"^(Edit|Write|MultiEdit|NotebookEdit)$|^Bash: sed -i"),
)]


def _signature(block) -> str:
    name, args = str(block.get("name") or "?"), block.get("input")
    args = args if isinstance(args, dict) else {}
    if name == "Bash" and isinstance(args.get("command"), str):
        words = _CD.sub("", args["command"]).split()[:2]
        return redact("Bash: " + " ".join(words))[:48]
    if name == "Skill" and isinstance(args.get("skill"), str):
        return "Skill: " + args["skill"][:60]
    if name in ("Agent", "Task") and isinstance(args.get("subagent_type"), str):
        return "Agent: " + args["subagent_type"][:60]
    return name[:60]


def _without_here_documents(text) -> str:
    """The command with each here-document body cut out: it is input to a program, not commands. One pass."""
    out, pos = [], 0
    while True:
        found = _HEREDOC.search(text, pos)
        eol = text.find("\n", found.end()) if found else -1
        if eol < 0:
            return "".join(out) + text[pos:]
        end = re.compile(rf"^[ \t]*{re.escape(found.group(2))}[ \t]*$", re.M).search(text, eol)
        if end is None:  # no end word: leave the rest alone
            return "".join(out) + text[pos:]
        out.append(text[pos:found.start()] + text[found.end():eol])
        pos = end.end()


RTK_OWN = {"gain", "verify", "discover", "init", "config", "hook", "--version", "--help"}  # rtk's own commands, not a wrapped one


def _step(piece, skip=NOISE) -> str:
    """The program and at most two plain words after it, or "" when the piece is no command. Never an argument's text."""
    tokens = piece.split()
    cmd = re.split(r"[\\/]", tokens[0])[-1] if tokens else ""  # .venv/Scripts/python.exe is python
    cmd = cmd[:-4] if cmd.lower().endswith(".exe") else cmd
    if cmd == "rtk" and len(tokens) > 1 and tokens[1] not in RTK_OWN:  # rtk wraps a command: the command is the step
        return _step(" ".join(tokens[2 if tokens[1] in ("bypass", "proxy") else 1:]), skip)
    if not _COMMAND.match(cmd) or cmd in skip:
        return ""
    rest = tokens[1:]
    if cmd == "git" and rest[:1] == ["-C"]:
        rest = rest[2:]
    while cmd in PYTHONS and rest and rest[0] in PYFLAGS:
        rest = rest[1:]
    kept = [cmd]
    for place, word in enumerate(rest[:2 if cmd in THREE else 1]):
        if not _WORD.match(word) or (place and word.startswith("-")):
            break
        kept.append(word)
    return redact("Bash: " + " ".join(kept))[:48]


def steps(block) -> list:
    """One signature per command of a tool call: a Bash call that chains several commands gives one for each."""
    args = block.get("input")
    if block.get("name") != "Bash" or not isinstance(args, dict) or not isinstance(args.get("command"), str):
        return [_signature(block)]
    command = _QUOTED.sub(" ", _without_here_documents(args["command"][:MAX_COMMAND]))  # a quoted ; or | is not a command
    pieces = _PIECES.split(command)
    def kept(found):
        return [s for s in found if s and s.split()[1] not in FILTERS and s != "Bash: sed -n"]

    found = kept(map(_step, pieces)) or kept(_step(p, ()) for p in pieces)  # the second pass: only noise
    if found:
        return list(dict.fromkeys(found))
    return [] if any(_step(p, ()) for p in pieces) else [_signature(block)]  # only filters: no step at all


# Phases that only route matching and the building blocks use (the workflows above do not count them: git status is in every turn).
INSPECT = re.compile(r"^Bash: git (status|diff|log|show)\b")
POLL = re.compile(r"\b(until|while)\b[^\n]*\bsleep\b|--watch\b|\bgh run watch\b")
CHAIN_ROUTE = {"test-loop": ["test"], "poll": ["poll"], "inspect": ["inspect"]}  # a building block's route, by the repeated chain it does


def block_phases(block) -> list:
    """The phases of work one tool call belongs to, in order: the ones in PHASES, plus inspect (git status, diff, log) and poll
    (a wait loop or a watch)."""
    out = [p for p in (phase(s) or ("inspect" if INSPECT.search(s) else "") for s in steps(block)) if p]
    args = block.get("input")
    if block.get("name") == "Bash" and isinstance(args, dict) and isinstance(args.get("command"), str) \
            and POLL.search(args["command"][:MAX_COMMAND]):
        out.append("poll")
    return out


def chains(route) -> list:
    """The building blocks a turn's phases (one per tool call, in order) show: test, read the failures, run again; wait until
    something is done; look at the git state before a commit."""
    return [name for name, found in (("test-loop", route.count("test") >= 2), ("poll", "poll" in route),
                                      ("inspect", route.count("inspect") >= 2 and "commit" in route)) if found]



def phase(sig) -> str:
    return next((name for name, pattern in PHASES if pattern.search(sig)), "")


# The same phases found in a playbook's prose steps ("git checkout -b ...", "Edit with ..."), for one with no recorded route.
_PROSE = [(name, re.compile(r"\b" + pattern.pattern[len("^Bash: "):])) for name, pattern in PHASES if pattern.pattern.startswith("^Bash: ")]
_PROSE.append(("edit", re.compile(r"\b(Edit|Write|sed -i)\b")))


def prose_phases(lines) -> set:
    """The phases the commands named in these step lines belong to. A match is blanked, so `gh pr checks` is ci and not also pr."""
    found = set()
    for line in lines:
        for name, pattern in _PROSE:
            line, hits = pattern.subn(" ", line)
            if hits:
                found.add(name)
    return found
