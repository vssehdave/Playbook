"""Claude Code hooks: offer a playbook while Claude works (PostToolUse) and when a request is typed (UserPromptSubmit), and
refuse a step done by hand that an offered playbook set to enforce covers (PreToolUse).

Each reads the hook's JSON from stdin and returns the text to print: hook JSON adding `additionalContext`, or "". Nothing
here raises: any error means no offer. They do nothing at all when a host that places offers itself launched Claude Code.
"""
import contextlib
import hashlib
import json
import os
import re
import time
from pathlib import Path

from . import config, offer, run as runner, store, thread
from .steps import block_phases
from .text import CONTINUE, handwritten, person_text

KEEP_SESSIONS = 50  # sessions remembered in sessions.json
KEEP_DAYS = 7
LOCK_STALE = 10  # seconds after which a left-over sessions.lock is someone's crash, not a writer
LOCK_WAIT = 1.0  # seconds a hook waits for the lock before it gives up saving
FREE = frozenset(("edit", "inspect"))  # phases never refused: reading and editing are how a failed run gets fixed


def project_names(cwd) -> list:
    """The names a playbook's `project` may use for this folder: its last two names joined by "/", and its last name."""
    parts = [p for p in re.split(r"[\\/]+", str(cwd or "")) if p]
    return [x for x in ("/".join(parts[-2:]), parts[-1] if parts else "") if x]


def _digest(text) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def _file() -> Path:
    return config.home() / "sessions.json"


def _load() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@contextlib.contextmanager
def _locked():
    """Yields True while this process holds sessions.lock (created with O_EXCL, so one holder on every platform), else False."""
    lock, held, until = _file().with_suffix(".lock"), False, time.time() + LOCK_WAIT
    while not held:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            held = True
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock.stat().st_mtime > LOCK_STALE:
                    lock.unlink()
                    continue
            if time.time() > until:
                break
            time.sleep(0.02)
        except OSError:
            break
    try:
        yield held
    finally:
        if held:
            with contextlib.suppress(OSError):
                lock.unlink()


def _save(key, entry):
    """Store one session's entry: load, change and replace the file under the lock, keeping the newest sessions only. Without
    the lock nothing is saved; the cost is an offer made twice."""
    with _locked() as held:
        if not held:
            return
        data = _load()
        data[key] = entry
        cut = time.time() - KEEP_DAYS * 86400
        keep = sorted(((k, v) for k, v in data.items() if isinstance(v, dict) and v.get("t", 0) >= cut), key=lambda kv: -kv[1].get("t", 0))
        tmp = _file().with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(keep[:KEEP_SESSIONS])), encoding="utf-8")
        os.replace(tmp, _file())


def _out(event, text) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}) if text else ""


def _called(messages) -> set:
    """The names of the playbooks the thread has run with playbook_run."""
    return {str(b["input"].get("name")) for m in messages if m.get("role") == "assistant" and isinstance(m.get("content"), list)
            for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use" and str(b.get("name")).endswith("playbook_run")
            and isinstance(b.get("input"), dict)}


def _thread(payload):
    """(messages, index of the message that starts this thread's turn, sub-agent type) or None when it cannot be read."""
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    kind, sidechain = "", False
    if payload.get("agent_id"):  # inside a sub-agent: its own file, beside the session's
        kind = str(payload.get("agent_type") or "")
        path = str(Path(path).with_suffix("") / "subagents" / f"agent-{payload['agent_id']}.jsonl")
        if not kind or not store.AGENT.match(kind):
            return None
        sidechain = True
    messages = thread.read(path, sidechain)
    typed = thread.typed_turns(messages)
    if not typed:
        return None
    seen = {b.get("id") for m in messages if isinstance(m.get("content"), list) for b in m["content"] if isinstance(b, dict)}
    if payload.get("tool_use_id") and payload["tool_use_id"] not in seen and isinstance(payload.get("tool_name"), str):
        given = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        messages.append({"role": "assistant", "uuid": "",
                         "content": [{"type": "tool_use", "id": payload["tool_use_id"], "name": payload["tool_name"], "input": given}]})
    return messages, typed[0] if kind else typed[-1], kind


def post_tool_use(payload) -> str:
    """An offer for the playbook two of three signals say covers the rest of this turn, once per playbook per turn."""
    cfg = config.settings()
    if config.hosted() or not (cfg["enabled"] and cfg["route"]):
        return ""
    found = _thread(payload)
    if found is None:
        return ""
    messages, start, kind = found
    key = f"{payload.get('session_id') or ''}:{kind or 'main'}"
    typed = person_text(messages[start])
    turn = messages[start].get("uuid") or _digest(typed)
    state = _load()
    mine = state.get(key) if isinstance(state.get(key), dict) else {}
    if mine.get("turn") != turn:
        mine = dict(mine, turn=turn, offered=[], ahead=None)
    offered = set(mine.get("offered") or ())
    if mine.get("prompt") == _digest(typed):  # the typed words already had a playbook offered
        offered.add(mine.get("word"))
    after = messages[start + 1:]
    cwd = payload.get("cwd")

    def ahead():
        if mine.get("ahead") is None:
            mine["ahead"] = bool(offer._ahead(cwd))
        return mine["ahead"]

    kept = offer.decide([m for m in after if m.get("role") == "assistant"], project_names(cwd), kind, offered, _called(after), cwd, ahead)
    if kept:
        offered.add(kept["name"])
    mine["offered"] = sorted(n for n in offered if n)
    mine["t"] = time.time()
    _save(key, mine)
    return _out("PostToolUse", kept["text"]) if kept else ""


def user_prompt_submit(payload) -> str:
    """The note of the playbook whose triggers match the typed request, when one does."""
    cfg = config.settings()
    prompt = payload.get("prompt")
    if config.hosted() or not (cfg["enabled"] and cfg["inject"]) or not isinstance(prompt, str):
        return ""
    low = handwritten(prompt)
    if len(low) < offer.MIN_REQUEST or CONTINUE.match(low):
        return ""
    best = offer.find(low, project_names(payload.get("cwd")), limit=1, agent_type="")
    if not best:
        return ""
    chosen = store.bump(best[0]["name"]) or best[0]
    state = _load()
    key = f"{payload.get('session_id') or ''}:main"
    typed = _digest(person_text({"role": "user", "content": prompt}))
    _save(key, dict(state.get(key) if isinstance(state.get(key), dict) else {}, prompt=typed, word=chosen["name"], t=time.time()))
    if cfg["auto_run"] and store.mode(chosen) == "auto" and offer.strong(chosen, low):
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) and Path(payload["cwd"]).is_dir() else None
        try:
            head, out, err, _ = runner._execute_auto(chosen["name"], cwd, runner.AUTO_TIMEOUT)
            return _out("UserPromptSubmit", offer.ran_note(chosen, head, out, err))
        except Exception:  # not automatic any more, or it would not start: the note, as for any match
            pass
    return _out("UserPromptSubmit", offer.note(chosen))


def _failed(messages) -> set:
    """The playbooks run with playbook_run in these messages whose result did not pass (a failure, an error, a refusal)."""
    ran = {b.get("id"): str(b["input"].get("name")) for m in messages if m.get("role") == "assistant" and isinstance(m.get("content"), list)
           for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_use" and str(b.get("name")).endswith("playbook_run")
           and isinstance(b.get("input"), dict)}
    out = set()
    for m in messages:
        for b in m.get("content") if m.get("role") == "user" and isinstance(m.get("content"), list) else ():
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in ran:
                body = b.get("content")
                text = body if isinstance(body, str) else " ".join(str(x.get("text") or "") for x in body or () if isinstance(x, dict))
                if b.get("is_error") or not text.lstrip().startswith("RESULT: pass"):
                    out.add(ran[b["tool_use_id"]])
    return out


def _deny(pb) -> str:
    given = ", ".join("<" + a["name"] + ">" for a in pb["args"])
    why = (f'Playbook {pb["name"]} is set to enforce: call playbook_run {{"name": "{pb["name"]}", "args": [{given}]}} instead of '
           f"doing this step by hand. If that run fails, the steps are yours again for the rest of this turn.")
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why}})


def pre_tool_use(payload) -> str:
    """Refuse a tool call whose phase (test, commit, push ...) is on the route of a playbook offered this turn and set to enforce,
    until a run of that playbook has not passed this turn. Reading, editing and the playbook tools themselves are never refused."""
    cfg = config.settings()
    name, given = payload.get("tool_name"), payload.get("tool_input")
    if config.hosted() or not (cfg["enabled"] and cfg["enforce"]) or not isinstance(name, str) or "playbook_" in name:
        return ""
    phases = set(block_phases({"type": "tool_use", "name": name, "input": given if isinstance(given, dict) else {}})) - FREE
    kind = str(payload.get("agent_type") or "") if payload.get("agent_id") else ""
    mine = _load().get(f"{payload.get('session_id') or ''}:{kind or 'main'}")
    if not phases or not isinstance(mine, dict) or not (mine.get("offered") or mine.get("word")):
        return ""
    found = _thread(payload)  # only now the session file: this hook runs before every tool call
    if found is None:
        return ""
    messages, start, kind = found
    typed = person_text(messages[start])
    names = set(mine.get("offered") or ()) if mine.get("turn") == (messages[start].get("uuid") or _digest(typed)) else set()
    if mine.get("prompt") == _digest(typed) and isinstance(mine.get("word"), str):
        names.add(mine["word"])
    names -= _failed(messages[start + 1:])
    for pb in filter(None, (store.load(n) for n in sorted(names) if store.valid_name(n))):
        if store.mode(pb) == "enforce" and pb["agent_type"] == kind and phases & set(store.route_of(pb)):
            return _deny(pb)
    return ""


EVENTS = {"post-tool-use": post_tool_use, "pre-tool-use": pre_tool_use, "user-prompt-submit": user_prompt_submit}


def run(event, raw) -> str:
    """The text to print for `playbook hook <event>` given the hook's stdin; "" on any error."""
    try:
        payload = json.loads(raw)
        return EVENTS[event](payload) if isinstance(payload, dict) and event in EVENTS else ""
    except Exception:  # a hook must never disturb the session
        return ""
