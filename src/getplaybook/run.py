"""Running an approved playbook: its included playbooks in order, then its own script, and what is reported back."""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from . import config
from .store import PLACEHOLDER, automatic, load, runnable, script_path, valid_name

RUN_TIMEOUT = 120  # seconds
RUN_OUTPUT = 20000  # characters kept of each of stdout and stderr
AUTO_TIMEOUT = 60  # seconds an automatic script may run before the request is sent
AUTO_OUTPUT = 3000  # characters of stdout and stderr together that go into the request
AUTO_LOCK = threading.Lock()  # one automatic script at a time; the store's lock is never held while one runs


class NotRun(Exception):
    """The script is not approved any more; the message is what Claude is told."""


def _execute(name, args, cwd=None, timeout=None) -> tuple:
    """Run an approved script. Returns (head, stdout, stderr, exit code or None on a timeout)."""
    pb = load(name) if valid_name(name) else None
    if pb is None or not pb["script"]:
        raise ValueError(f"no playbook called {name!r} has a script")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("args must be a list of strings")
    if not runnable(pb):
        why = "its script changed after approval" if pb["hash"] else "its script is a draft"
        raise NotRun(f"Not run: {why}. The owner approves it with `{config.CLI} approve {name}`. "
                     f"Meanwhile run it yourself with Bash: python \"{script_path(name)}\" --help")
    cwd = cwd if cwd and Path(cwd).is_dir() else None
    try:  # shortcut: the hash is checked just before the start, not held across it; fine for a folder only the owner writes to
        done = subprocess.run([sys.executable, "-I", str(script_path(name)), *args], shell=False, cwd=cwd, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=timeout or RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out, err = (x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "") for x in (exc.stdout, exc.stderr))
        return f"timed out after {timeout or RUN_TIMEOUT} seconds and was stopped", out, err, None
    return f"exit code {done.returncode}", done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace"), done.returncode


def _kill_tree(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
        proc.wait(timeout=10)
    except Exception:
        pass


def _first_chars(handle) -> str:
    size = handle.seek(0, 2)
    handle.seek(0)
    text = handle.read(AUTO_OUTPUT * 4).decode("utf-8", "replace")[:AUTO_OUTPUT]
    return text + ("\n[output cut]" if size > len(text.encode("utf-8")) else "")


def _execute_auto(name, cwd, timeout) -> tuple:
    """Run an automatic script for a host that runs them itself. Output goes to temporary files (so nothing big is held in memory),
    and at the limit the whole process tree is stopped. Returns (head, stdout, stderr, exit code or None)."""
    pb = load(name)
    if pb is None or not automatic(pb):
        raise NotRun(f"{name} is not automatic any more")
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    with tempfile.TemporaryFile() as fout, tempfile.TemporaryFile() as ferr:
        proc = subprocess.Popen([sys.executable, "-I", str(script_path(name))], shell=False, cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=fout, stderr=ferr, **flags)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            code = None
        head = f"exit code {code}" if code is not None else f"timed out after {timeout} seconds and was stopped"
        return head, _first_chars(fout), _first_chars(ferr), code


MAX_DEPTH = 4  # how deep playbooks may include playbooks
PASS_OUTPUT = 2000  # characters of a passing script's stdout that are shown
FAIL_OUTPUT = 4000  # characters of the failing part that are shown
_FAILED_TESTS = re.compile(r"(?m)^(?:(?:FAIL|ERROR): (\S+ \([\w.]+\))|FAILED (\S+::\S+))")


def plan(name, depth=0, seen=()) -> list:
    """The scripts to run for a playbook, in order: what it includes (each flattened), then its own script. ValueError when
    an included playbook is missing or the includes loop or nest too deep."""
    pb = load(name)
    if pb is None:
        raise ValueError(f"no usable playbook called {name!r}")
    if name in seen or depth > MAX_DEPTH:
        raise ValueError(f"playbook {name!r} includes itself or nests too deep")
    out = [step for inner in pb["includes"] for step in plan(inner, depth + 1, seen + (name,))]
    return list(dict.fromkeys(out + ([name] if pb["script"] else [])))


def inner_names(name, depth=0) -> set:
    """Every playbook `name` includes, at any depth."""
    pb = load(name) if valid_name(name) else None
    if pb is None or depth > MAX_DEPTH:
        return set()
    return set(pb["includes"]).union(*(inner_names(n, depth + 1) for n in pb["includes"]))


def _agent_call(pb, args) -> str:
    """The Agent tool input for the playbook's delegate, with the args filled in by position; ValueError when one is missing."""
    names = [a["name"] for a in pb["args"]]
    values = dict(zip(names, args))
    used = PLACEHOLDER.findall(pb["delegate"]["prompt"])
    missing = [n for n in names if n in used and n not in values]
    if missing:
        raise ValueError(f"{pb['name']} needs the argument {missing[0]!r} (args in order: {', '.join(names)})")
    prompt = PLACEHOLDER.sub(lambda m: values[m.group(1)], pb["delegate"]["prompt"])
    call = {"subagent_type": pb["delegate"]["agent"], "description": pb["title"][:60], "prompt": prompt}
    return ("AGENT CALL: " + json.dumps(call, ensure_ascii=False)
            + "\nSend that call with the Agent tool as it is; do not rewrite the prompt.")


def run(name, args, cwd=None, timeout=None, start=None) -> str:
    """Run an approved playbook: its included playbooks in order, then its own script, stopping at the first failure.
    `start` (a step's name, or its number from 1) continues a run that stopped. Returns the report for Claude, which
    leads with `RESULT: pass` or `RESULT: fail (...)`; raises ValueError when it must not run."""
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("args must be a list of strings")
    steps = plan(name) if valid_name(name) else []
    named = load(name) if valid_name(name) else None
    delegate = named["delegate"] if named else None
    if not steps and not delegate:
        raise ValueError(f"no playbook called {name!r} has a script")
    # shortcut: only the named playbook's delegate is used, the delegates of included playbooks are ignored; change when a
    # playbook should be able to hand off through the playbooks it includes.
    call = _agent_call(named, args) if delegate else ""  # filled first, so a missing argument stops the run before any script
    if delegate and not steps and not runnable(named):
        why = "its delegate changed after approval" if named["hash"] else "its delegate is a draft"
        return _failure(name, [name], 0, "not run", f"Not run: {why}. The owner approves it with `{config.CLI} approve {name}`.", args, False)
    first = 0
    if start not in (None, ""):
        word = str(start).strip()
        first = int(word) - 1 if word.isdigit() else steps.index(word) if word in steps else -1
        if not 0 <= first < len(steps):
            raise ValueError(f"{start!r} is not a step of {name}: its steps are {', '.join(steps)}")
    many, out = len(steps) > 1, ""
    for place, step in enumerate(steps[first:], first):
        try:
            head, out, err, code = _execute(step, args if step == name else [], cwd, timeout)
        except NotRun as why:
            return _failure(name, steps, place, "not run", str(why), args, many)
        if code != 0:
            return _failure(name, steps, place, head if code is None else f"exit {code}", _failing(out, err), args, many)
    ran = f"Steps run: {', '.join(steps[first:])}\n" if many else ""
    report = f"RESULT: pass\n{ran}" + (f"--- stdout ---\n{_tail(out, PASS_OUTPUT)}" if steps[first:] else "")
    return report + (("\n" if steps[first:] else "") + call if call else "")


def _failure(name, steps, place, why, body, args, many) -> str:
    text = f"RESULT: fail ({why})\n" + (f"FAILED STEP: {steps[place]} ({place + 1} of {len(steps)})\n" if many else "") + body
    if many:  # the model fixes the cause, then goes on from that step: the steps before it are not run again
        resume = {"name": name, "from": steps[place], **({"args": args} if args else {})}
        text += f"\nRESUME: playbook_run {json.dumps(resume)}"
    return text


def _cap(text, limit) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n[cut: {len(text) - limit} more characters]"


def _failing(out, err) -> str:
    """Only what failed: the names and tracebacks of failing tests when the output is a unittest or pytest run, else the end of it."""
    text = (err + "\n" + out).replace("\r\n", "\n")
    names = [a or b for a, b in _FAILED_TESTS.findall(text)]
    found = re.search(r"(?m)^={70}\n(?:FAIL|ERROR): ", text)
    if found:  # unittest: every failure's block, up to the line that counts the run
        end = re.search(r"(?m)^-{70}\nRan \d+ tests?", text[found.start():])
        body = text[found.start():found.start() + end.start()] if end else text[found.start():]
    elif (found := re.search(r"(?m)^=+ (FAILURES|ERRORS) =+$", text)):
        end = re.search(r"(?m)^=+ short test summary info =+$", text[found.start():])
        body = text[found.start():found.start() + end.start()] if end else text[found.start():]
    else:  # a script's own failure report may be on either stream (a JSON line on stdout, git's complaint on stderr): the end of both
        body = "\n".join(f"--- {label} ---\n{_tail(text_, FAIL_OUTPUT // 2)}" for label, text_ in (("stdout", out), ("stderr", err)) if text_.strip())
    head = f"Failing: {', '.join(list(dict.fromkeys(names))[:20])}\n" if names else ""
    return head + _cap(body.strip(), FAIL_OUTPUT)


def _tail(text, limit) -> str:
    """The last 20 lines of the text, at most `limit` characters: what a script prints last (a link, a result) is what matters."""
    text = "\n".join(text.replace("\r\n", "\n").strip().split("\n")[-20:])
    return text if len(text) <= limit else f"[cut: {len(text) - limit} earlier characters]\n" + text[-limit:]

