"""Playbooks on disk: the folder format, checking, approval and signing, one playbook per route.

A playbook is a folder, `<home>/playbooks/<name>/`, holding `playbook.json` (when it applies, an ordered
decision list, the skills and agents used) and, optionally, `run.py`, a script that does the whole job
with no model in the loop. A script runs only after the owner approved the exact text with
`playbook approve`.
"""
import hashlib
import hmac
import json
import re
import secrets
import shutil
import time
from pathlib import Path

from . import config
from .steps import prose_phases

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
WORD = re.compile(r"[a-z0-9]+")
AGENT = re.compile(r"^[\w:.-]*$")
DELEGATE_AGENT = re.compile(r"^[A-Za-z0-9_:-]{1,60}$")
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_-]*)\}")  # the only braces that count in a delegate prompt
JSON_LIMIT = 20 * 1024
SCRIPT_LIMIT = 200 * 1024
MAX_STEPS = 30
SOURCES = ("claude", "miner", "owner")
STATUSES = ("draft", "approved")
MODES = ("suggest", "enforce", "auto")


def root() -> Path:
    return config.home() / "playbooks"



def valid_name(name) -> bool:
    return isinstance(name, str) and bool(NAME.match(name))


def folder(name) -> Path:
    if not valid_name(name):
        raise ValueError(f"a playbook name is lowercase letters, digits and hyphens, 2 to 49 characters: {str(name)[:60]!r}")
    return root() / name


FRAME = re.compile(r"<\s*/?\s*system-reminder", re.I)


def _framed(value, key):
    if FRAME.search(value):
        raise ValueError(f"{key} must not contain a system-reminder tag")


def _text(data, key, low, high, required=True) -> str:
    value = data.get(key, "" if not required else None)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    _framed(value, key)
    value = value.strip()
    if len(value) > high or (required and len(value) < low):
        raise ValueError(f"{key} must be {low} to {high} characters")
    return value


def _list(data, key, count, size, required=False) -> list:
    value = data.get(key, [])
    if not isinstance(value, list) or len(value) > count or (required and not value):
        raise ValueError(f"{key} must be a list of at most {count} items" + (" and not empty" if required else ""))
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > size:
            raise ValueError(f"every item of {key} must be text of at most {size} characters")
        _framed(item, key)
        out.append(item.strip())
    return out


def _delegate(data, arg_names):
    """The ready-made sub-agent call {agent, prompt}, or None. Every {placeholder} in the prompt must be one of the args."""
    given = data.get("delegate")
    if given is None:
        return None
    if not isinstance(given, dict) or not isinstance(given.get("agent"), str) or not DELEGATE_AGENT.match(given["agent"]):
        raise ValueError("delegate is {agent, prompt}; agent is a plain agent name of 1 to 60 characters (letters, digits, - _ :)")
    prompt = _text(given, "prompt", 1, 4000)
    unknown = sorted(set(PLACEHOLDER.findall(prompt)) - set(arg_names))
    if unknown:
        raise ValueError(f"delegate prompt uses {{{unknown[0]}}}, which is not one of the args")
    return {"agent": given["agent"], "prompt": prompt}


def clean(data) -> dict:
    """The playbook as it is stored, or ValueError. Types and sizes are checked here, on every load and save."""
    if not isinstance(data, dict):
        raise ValueError("a playbook is a JSON object")
    triggers = [t.lower() for t in _list(data, "triggers", 10, 60, required=True)]
    if not all(WORD.search(t) for t in triggers):
        raise ValueError("every trigger needs at least one word")
    args = data.get("args", [])
    if not isinstance(args, list) or len(args) > 10:
        raise ValueError("args must be a list of at most 10 items")
    for arg in args:
        if not isinstance(arg, dict) or not isinstance(arg.get("name"), str) or not 0 < len(arg["name"]) <= 40 \
                or not isinstance(arg.get("help", ""), str) or len(arg.get("help", "")) > 200:
            raise ValueError("each arg is {name, help}, with a name of at most 40 characters and help of at most 200")
    uses, script, auto = data.get("uses", 0), data.get("script", False), data.get("auto", False)
    if type(uses) is not int or uses < 0 or type(script) is not bool or type(auto) is not bool:
        raise ValueError("uses must be a whole number and script and auto true or false")
    status, source = data.get("status", "draft"), data.get("source", "claude")
    if status not in STATUSES or source not in SOURCES:
        raise ValueError(f"status is one of {STATUSES} and source one of {SOURCES}")
    agent_type = _text(data, "agent_type", 0, 40, required=False)
    includes = _list(data, "includes", 10, 49)
    if not AGENT.match(agent_type) or not all(valid_name(n) for n in includes) or data.get("name") in includes:
        raise ValueError("agent_type is a plain agent name, and includes lists other playbooks by name")
    out = {"name": data.get("name"), "title": _text(data, "title", 1, 120), "when": _text(data, "when", 1, 300),
           "triggers": triggers, "project": _text(data, "project", 0, 200, required=False),
           "steps": _list(data, "steps", MAX_STEPS, 400, required=True),
           "phases": _list(data, "phases", 20, 40), "skills": _list(data, "skills", 20, 80), "agents": _list(data, "agents", 20, 80),
           "script": script, "args": [{"name": a["name"], "help": a.get("help", "")} for a in args],
           "auto": auto, "status": status, "hash": _text(data, "hash", 0, 64, required=False), "uses": uses,
           "created": _text(data, "created", 0, 32, required=False), "source": source,
           "route": _list(data, "route", 20, 40), "agent_type": agent_type, "includes": includes,
           "delegate": _delegate(data, [a["name"] for a in args])}
    if not valid_name(out["name"]):
        raise ValueError("bad name")
    return out


def _read(path: Path, limit: int):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        return None
    return path.read_bytes()


def load(name):
    """The stored playbook, or None when it is missing or bad. Never raises."""
    try:
        raw = _read(folder(name) / "playbook.json", JSON_LIMIT)
        pb = clean(json.loads(raw.decode("utf-8"))) if raw is not None else None
        if pb is None or pb["name"] != name:
            return None
        if pb["script"] and _read(folder(name) / "run.py", SCRIPT_LIMIT) is None:
            return None
        return pb
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return None


def names() -> list:
    try:
        return sorted(p.name for p in root().iterdir() if valid_name(p.name) and p.is_dir() and not p.is_symlink())
    except OSError:
        return []


def listing() -> list:
    return [pb for pb in map(load, names()) if pb]


def script_path(name) -> Path:
    return folder(name) / "run.py"


def script_hash(name) -> str:
    try:
        raw = _read(script_path(name), SCRIPT_LIMIT)
    except (OSError, ValueError):
        return ""
    return hashlib.sha256(raw).hexdigest() if raw is not None else ""


def approval_hash(pb) -> str:
    """What the owner approves: the script's hash, as it always was, when there is no delegate; with one, a hash over
    the script's bytes (if any) and the delegate, so editing either sends the playbook back to draft."""
    digest = script_hash(pb["name"])
    if not pb.get("delegate"):
        return digest
    raw = _read(script_path(pb["name"]), SCRIPT_LIMIT) if pb["script"] else None
    canon = json.dumps(pb["delegate"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256((raw or b"") + canon).hexdigest()


def runnable(pb) -> bool:
    """An approved script or delegate whose text still matches the approved hash."""
    return bool((pb["script"] or pb["delegate"]) and pb["status"] == "approved" and pb["hash"] and pb["hash"] == approval_hash(pb))


def auto_file() -> Path:
    """The owner's approvals for enforce and auto: {name: "<script hash>:<mode>:<signature>"}. Written only by the CLI, never inside a playbook folder."""
    return config.home() / "auto.json"


def _auto_key(make=False):
    """The CLI's own random key, kept beside auto.json. A model that can write files but not run code cannot sign an
    entry with it, so copying an approved script's hash into auto.json no longer makes it automatic."""
    path = config.home() / "auto.key"
    key = _read(path, 1024)
    if (not key or len(key) < 32) and make:
        config.home().mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        path.write_bytes(key)
    return key if key and len(key) >= 32 else None


def _auto_mac(name, digest, mode, key) -> str:
    """The signature over name, hash and mode; an entry from before modes (`<hash>:<signature>`) signed name and hash only."""
    text = f"{name}\0{digest}" + (f"\0{mode}" if mode else "")
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def _auto_read() -> dict:
    try:
        raw = _read(auto_file(), JSON_LIMIT)
        data = json.loads(raw.decode("utf-8")) if raw is not None else {}
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(data, dict) else {}


def _auto_set(name, digest=None, mode="auto"):
    """Sign the owner's approval of `mode` for the script with this hash (empty for a playbook without a script); no hash removes it."""
    data = _auto_read()
    if digest is not None:
        data[name] = f"{digest}:{mode}:{_auto_mac(name, digest, mode, _auto_key(make=True))}"
    elif name in data:
        del data[name]
    else:
        return
    config.home().mkdir(parents=True, exist_ok=True)
    auto_file().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _signed(pb) -> str:
    """The mode the owner's signed entry gives this playbook's current hash: "enforce", "auto" or "" (none, forged or for other text)."""
    key, parts = _auto_key(), _auto_read().get(pb["name"], "").split(":")
    if not key or len(parts) not in (2, 3):
        return ""
    legacy = len(parts) == 2
    digest, mode, mac = (parts[0], "auto", parts[1]) if legacy else parts
    ok = hmac.compare_digest(mac, _auto_mac(pb["name"], digest, "" if legacy else mode, key))
    return mode if ok and digest == pb["hash"] and mode in ("enforce", "auto") else ""


def mode(pb) -> str:
    """How the playbook is offered, from the owner's signed entry only: "suggest" (a note), "enforce" (hand-run steps it covers
    are refused) or "auto" (a host runs it before the model sees the request). Anything unsigned, changed or no longer
    valid for its mode (not approved, script edited, auto with arguments) is "suggest"."""
    signed = _signed(pb)
    if pb["status"] != "approved":
        return "suggest"
    if signed == "auto" and pb["auto"] and not pb["args"] and runnable(pb):
        return "auto"
    return "enforce" if signed == "enforce" and (runnable(pb) or (pb["includes"] and not pb["script"])) else "suggest"


def automatic(pb) -> bool:
    """Would the host run this script by itself now: signed as auto, no args, still approved, and the entry names this exact script."""
    return mode(pb) == "auto"


def shown_status(pb) -> str:
    """The status for `list` and `show`: a script edited after approval is a draft again, and only a live enforce or auto one says so."""
    if (pb["script"] or pb["delegate"]) and pb["status"] == "approved" and not runnable(pb):
        return "draft (changed)" if pb["delegate"] else "draft (script changed)"
    return pb["status"] + (f", {mode(pb)}" if mode(pb) != "suggest" else "")


def _no_link(*paths):
    """Refuse to write through a symlink: a link planted in a playbook folder would redirect the write elsewhere."""
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"{path.name} is a symbolic link: {config.NAME} will not write through it")


def _write(pb):
    text = json.dumps(pb, indent=2, ensure_ascii=False) + "\n"
    if len(text.encode("utf-8")) > JSON_LIMIT:
        raise ValueError(f"a playbook's JSON is at most {JSON_LIMIT} bytes")
    _no_link(folder(pb["name"]), folder(pb["name"]) / "playbook.json")
    folder(pb["name"]).mkdir(parents=True, exist_ok=True)
    (folder(pb["name"]) / "playbook.json").write_bytes(text.encode("utf-8"))


def save(data, script=None):
    """Validate and store a playbook. Returns the stored dict.

    status, hash, auto, uses and created are not taken from the caller: what the model or the miner
    saves is a draft until the owner approves it, a script is approved only while its text is the one the owner approved, and a saved
    playbook is never automatic: only `playbook approve --mode auto` makes it so, so a save also
    drops the owner's automatic entry.
    """
    if not isinstance(data, dict) or not valid_name(data.get("name")):
        raise ValueError("a playbook needs a valid name")
    name = data["name"]
    old = load(name)
    if script is not None and (not isinstance(script, str) or len(script.encode("utf-8")) > SCRIPT_LIMIT):
        raise ValueError(f"script must be text of at most {SCRIPT_LIMIT} bytes")
    wants = script is not None or (data.get("script") is not False and old is not None and old["script"])
    pb = clean(dict(data, script=bool(wants), status="draft", hash="", auto=False, uses=old["uses"] if old else 0,
                    created=old["created"] if old else time.strftime("%Y-%m-%d")))
    if script is not None:
        pb["script"] = True
        _no_link(folder(name), script_path(name))
        folder(name).mkdir(parents=True, exist_ok=True)
        script_path(name).write_bytes(script.encode("utf-8"))
    elif not pb["script"] and script_path(name).exists():
        script_path(name).unlink()
    if not pb["script"] and not pb["delegate"]:
        pb["status"] = "approved" if pb["source"] == "owner" else "draft"  # what a model or the miner wrote waits for the owner
    elif old and old["status"] == "approved" and old["hash"] and old["hash"] == approval_hash(pb):
        pb["status"], pb["hash"] = "approved", old["hash"]
    _write(pb)
    _auto_set(name)
    return pb


def approve(name, mode="suggest", auto=False):
    """Record the hash of run.py as it is now, and how the playbook is offered (see `mode`): `enforce` also refuses hand-run
    steps it covers, `auto` lets the host run it before the model sees a request. The mode is signed with the CLI's key,
    not stored in the playbook. `auto=True` is the older way to say mode "auto". Returns (hash, text of run.py)."""
    mode = "auto" if auto else mode
    if mode not in MODES:
        raise ValueError(f"mode is one of {', '.join(MODES)}")
    pb = load(name)
    if pb is None:
        raise ValueError(f"no usable playbook called {name!r}")
    if pb["delegate"] and mode == "auto":
        raise ValueError(f"mode auto is refused: {name} hands work to a sub-agent, which a host cannot start before the model sees a request")
    if not pb["script"] and pb["delegate"]:
        pb["status"], pb["auto"], pb["hash"] = "approved", False, approval_hash(pb)
        _write(pb)
        _auto_set(name, pb["hash"] if mode == "enforce" else None, mode)
        return pb["hash"], ""
    if not pb["script"]:
        if mode == "auto" or (mode == "enforce" and not pb["includes"]):
            raise ValueError(f"mode {mode} is refused: {name} has no script" + (" and includes nothing to run" if mode == "enforce" else ""))
        pb["status"], pb["auto"] = "approved", False
        _write(pb)
        _auto_set(name, "" if mode == "enforce" else None, mode)
        return "", ""
    if mode == "auto" and pb["args"]:
        raise ValueError(f"mode auto is refused: {name} takes arguments, and a script that needs inputs cannot run from the words of a request")
    if mode == "auto" and not any(len(WORD.findall(t)) >= 2 for t in pb["triggers"]):
        raise ValueError(f"mode auto is refused: {name} has no trigger of two or more words, and one word is too little to start a script")
    raw = _read(script_path(name), SCRIPT_LIMIT)
    pb["status"], pb["hash"], pb["auto"] = "approved", approval_hash(pb), mode == "auto"
    _write(pb)
    _auto_set(name, pb["hash"] if mode != "suggest" else None, mode)
    return pb["hash"], raw.decode("utf-8", "replace")


def remove(name) -> bool:
    path = folder(name)
    if not path.is_dir() or path.is_symlink():
        return False
    shutil.rmtree(path)
    _auto_set(name)
    return True


def bump(name):
    pb = load(name)
    if pb:
        pb["uses"] += 1
        _write(pb)
    return pb


# ---- one playbook per route ----------------------------------------------------------------------

SAME_ROUTE = 0.8  # Jaccard of two playbooks' phases for them to be the same way of working
SHIPS = frozenset("commit push pr merge ci release".split())  # a route with one of these ends in something that is shipped


def route_of(pb) -> list:
    """The phases of work a playbook's route goes through, in order: `route`, else the miner's `phases`."""
    out = []
    for phase in pb.get("route") or pb.get("phases") or ():
        if not out or out[-1] != phase:
            out.append(phase)
    return out


def _phases(pb) -> set:
    """The route's phases, or for a playbook with no route the phases its steps name."""
    return set(route_of(pb)) or prose_phases(pb.get("steps") or ())


def similar(a, b) -> bool:
    x, y = _phases(a), _phases(b)
    return bool(x and y) and a.get("agent_type", "") == b.get("agent_type", "") and a.get("project", "") == b.get("project", "") \
        and len(x & y) / len(x | y) >= SAME_ROUTE


def twin(data):
    """The stored playbook that does the same route as this playbook's data (approved first, then most used), or None."""
    hits = [pb for pb in listing() if similar(pb, data)]
    return min(hits, key=lambda pb: (pb["status"] != "approved", -pb["uses"], pb["name"]), default=None)


def absorb(keep, other):
    """Add the triggers of `other` (a playbook or its data) to the stored playbook `keep`, whose script and approval stay as
    they are. An automatic playbook is left alone: new words must not be able to start its script."""
    if automatic(keep):
        return keep
    given = other.get("triggers")
    extra = [t.strip().lower() for t in given if isinstance(t, str) and WORD.search(t)] if isinstance(given, list) else []
    merged = list(dict.fromkeys(keep["triggers"] + [t for t in extra if len(t) <= 60]))[:10]
    if merged != keep["triggers"]:
        keep = dict(keep, triggers=merged)
        _write(keep)
    return keep


def dedupe(include_approved=False, out=print, dry_run=False) -> int:
    """Merge playbooks that do the same route into one (an approved one first, then one with a script, then the most used).
    Approved playbooks are left alone unless asked for. With `dry_run` it only says what it would merge.
    Returns how many were merged away."""
    books = [pb for pb in listing() if include_approved or pb["status"] != "approved"]
    books.sort(key=lambda pb: (pb["status"] != "approved", not pb["script"], -pb["uses"], pb["created"], pb["name"]))
    used = {n for pb in listing() for n in pb["includes"]}  # one that others include keeps its name
    gone = set()
    for i, keep in enumerate(books):
        for other in books[i + 1:]:
            if keep["name"] in gone or other["name"] in gone or other["name"] in used or not similar(keep, other):
                continue
            if not dry_run:
                keep = absorb(load(keep["name"]) or keep, other)
                remove(other["name"])
            gone.add(other["name"])
            out(f"{'would merge' if dry_run else 'merged'} {other['name']} into {keep['name']}")
    return len(gone)
