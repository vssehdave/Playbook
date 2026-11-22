"""The offer log: one JSON line per event in log.jsonl, to measure how often an offer is taken. Names and counts only: never
the request, a tool's input or its output."""
import json
import time

from . import config

EVENTS = ("offer", "take", "deny", "pass", "fail")
LABELS = dict(zip(EVENTS, ("offers", "takes", "denies", "passes", "fails")))


def _path():
    logs = config.home() / "logs"  # beside the miner's log when the home has that folder
    return (logs if logs.is_dir() else config.home()) / "log.jsonl"


def write(event, payload, playbook, mode):
    """Append one event; never raises. `payload` is the hook's: only session_id and, in a sub-agent, agent_type are read."""
    try:
        sub = str(payload.get("agent_type") or "") if payload.get("agent_id") else ""
        row = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "session_id": str(payload.get("session_id") or ""), "agent_type": sub,
               "playbook": str(playbook), "mode": str(mode), "event": event}
        with open(_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    except Exception:
        pass


def stats() -> list:
    """Lines `name  offers N  takes N  denies N  passes N  fails N`, one per playbook in the log."""
    counts = {}
    try:
        lines = _path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for text in lines:
        try:
            row = json.loads(text)
            counts.setdefault(row["playbook"], dict.fromkeys(EVENTS, 0))[row["event"]] += 1
        except (ValueError, KeyError, TypeError):
            continue
    return [f"{name:<28} " + "  ".join(f"{LABELS[e]} {c[e]}" for e in EVENTS)
            for name, c in sorted(counts.items())]
