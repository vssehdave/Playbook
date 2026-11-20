"""Claude Code hooks: offer a playbook while Claude works (PostToolUse) and when a request is typed (UserPromptSubmit).

Each reads the hook's JSON from stdin and returns the text to print: hook JSON adding `additionalContext`, or "". Nothing
here raises: any error means no offer. They do nothing at all when a host that places offers itself launched Claude Code.
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

from . import config, offer, store, thread
from .text import CONTINUE, handwritten, person_text

KEEP_SESSIONS = 50  # sessions remembered in sessions.json
KEEP_DAYS = 7


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


def _save(data):
    """Newest sessions only. shortcut: a plain replace, not a lock: two hooks in one instant may lose one's note, and the
    cost is an offer made twice; upgrade if that ever shows."""
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
    state[key] = mine
    _save(state)
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
    state[key] = dict(state.get(key) if isinstance(state.get(key), dict) else {}, prompt=typed, word=chosen["name"], t=time.time())
    _save(state)
    return _out("UserPromptSubmit", offer.note(chosen))


EVENTS = {"post-tool-use": post_tool_use, "user-prompt-submit": user_prompt_submit}


def run(event, raw) -> str:
    """The text to print for `playbook hook <event>` given the hook's stdin; "" on any error."""
    try:
        payload = json.loads(raw)
        return EVENTS[event](payload) if isinstance(payload, dict) and event in EVENTS else ""
    except Exception:  # a hook must never disturb the session
        return ""
