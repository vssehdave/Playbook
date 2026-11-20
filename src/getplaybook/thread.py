"""Reading a Claude Code session file as the messages of one thread, for the hooks."""
import json

from .text import person_text

WINDOW = 3_000_000  # bytes read from the end of a session file: the turn in progress is at the end


def read(path, sidechain=False, window=WINDOW) -> list:
    """The messages of a session file in order, as the dicts of the Messages API ({role, content, uuid}). Claude Code writes
    one line per content block, so consecutive assistant lines of one reply are joined. A sub-agent's file (`sidechain`) is
    all sidechain lines; the main file skips them. Never raises."""
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, 2)
            handle.seek(max(0, size - window))
            raw = handle.read().decode("utf-8", "replace")
    except (OSError, TypeError, ValueError):
        return []
    lines = raw.split("\n")[1:] if size > window else raw.split("\n")
    out, last_id = [], None
    for line in lines:
        if '"user"' not in line and '"assistant"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg = d.get("message") if isinstance(d, dict) else None
        if not isinstance(msg, dict) or d.get("type") not in ("user", "assistant") or bool(d.get("isSidechain")) != sidechain:
            continue
        origin = d.get("origin")
        if d["type"] == "user" and (d.get("isMeta") or (isinstance(origin, dict) and origin.get("kind") not in (None, "human"))):
            continue
        content = msg.get("content")
        if d["type"] == "assistant" and out and out[-1]["role"] == "assistant" and last_id and msg.get("id") == last_id \
                and isinstance(content, list) and isinstance(out[-1]["content"], list):
            out[-1]["content"] += content
            continue
        last_id = msg.get("id") if d["type"] == "assistant" else None
        out.append({"role": d["type"], "content": content if isinstance(content, (str, list)) else [], "uuid": str(d.get("uuid") or "")})
    return out


def typed_turns(messages) -> list:
    """Indexes of the messages a person typed."""
    return [i for i, m in enumerate(messages) if person_text(m)]
