"""Find tasks the owner repeats in past Claude Code sessions, and keep them as candidates for a playbook.

Mining uses no model: it reads Claude Code's own session files (read-only), keeps the text of the
requests the person typed (redacted, cut short) and the names of the tools that followed, never any
tool result, and groups multi-step turns with plain code, in two ways: requests worded alike, and
turns worked through alike whatever their words (the same phases: branch, edit, test, commit, pull
request ...). `build.py` has the compiling.
"""
import hashlib
import json
import re
import statistics
import time
from collections import Counter
from pathlib import Path

from . import config, offer, store
from .steps import CHAIN_ROUTE, _signature, block_phases, chains, phase, steps
from .text import person_text, redact

# Extra sources of candidates a host program may add: each is called as feed(found, cfg) and returns more candidates.
FEEDS = []

MIN_TOOLS = 3  # a turn with fewer tool calls is not multi-step
SIMILAR = 0.45
TEXT_LIMIT = 600
KEEP = 20  # candidates saved
COMMON = 0.6  # share of a cluster's turns a tool signature must appear in
FRESH = 24 * 3600  # the candidates file is mined again when older than this
RETRY = 7 * 24 * 3600  # a candidate that failed waits this long
TIMEOUT = 600
LOCK_STALE = 3 * 3600
STOP = set("""the and for that this with from have has had are was were you your not but can could would should will just
into out about then than them they what when where which while who how why all any some more most other our its let lets
please also one two get got make made need want use using used there here been being does did doing done like now new
too very yes yeah okay sure thanks thank still again next first last same each every over under after before see""".split())
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_DROP = re.compile(r"https?://\S+|\S*[\\/]\S*|\S*\d\S*")  # urls, paths, numbers, hashes
SHAPE_SIMILAR = 0.75  # share of phases two turns must have in common to count as the same way of working
MIN_PHASES = 3  # fewer is ordinary editing or a question, not a way of working


def candidates_path() -> Path:
    return store.root() / "_candidates.json"


def state_path() -> Path:
    return store.root() / "_state.json"


def _load(path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---- reading the session files -------------------------------------------------------------------

def config_dirs(cfg=None) -> list:
    """Claude Code config folders to read: ~/.claude, $CLAUDE_CONFIG_DIR and the `transcript_dirs` setting."""
    return config.transcript_dirs(cfg)


def _typed(d):
    """The text the person typed in this user line, or None when it is not a typed turn."""
    msg = d.get("message")
    origin = d.get("origin")
    if d.get("isMeta") or not isinstance(msg, dict) or (isinstance(origin, dict) and origin.get("kind") not in (None, "human")):
        return None
    text = person_text({"role": "user", "content": msg.get("content")})
    text = " ".join(redact(_REMINDER.sub(" ", text)).split())
    return text if text and not text.startswith("<task-notification") else ""


def _end(turn, out):
    if turn and len(turn["tools"]) >= MIN_TOOLS:
        turn["chains"] = chains(turn.pop("route"))
        turn["requests"] = len(turn["usage"])
        turn["tokens"] = sum(turn.pop("usage").values())
        out.append(turn)


def read_file(path, session_folder, cutoff_iso, agent_type=None) -> list:
    """The typed turns of one session file that were followed by at least MIN_TOOLS tool calls. A sub-agent's file
    (`agent_type` given) is all sidechain lines, and its one typed turn is the task its parent gave it."""
    out, turn = [], None
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with handle:
        for line in handle:
            if '"user"' not in line and '"assistant"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict) or (d.get("isSidechain") is True and agent_type is None) or d.get("type") not in ("user", "assistant"):
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if d["type"] == "user":
                if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                    continue
                text = _typed(d)
                if text is None:
                    continue
                _end(turn, out)
                stamp = d.get("timestamp")
                if not text or (isinstance(stamp, str) and stamp < cutoff_iso):
                    turn = None
                    continue
                cwd = d.get("cwd") if isinstance(d.get("cwd"), str) else ""
                parts = [p for p in re.split(r"[\\/]+", cwd) if p]
                turn = {"text": text[:TEXT_LIMIT], "session": str(d.get("sessionId") or Path(path).stem), "at": str(stamp),
                        "project": "/".join(parts[-2:]) or session_folder, "tools": [], "steps": [], "skills": [], "agents": [], "usage": {}, "route": [],
                        "agent_type": agent_type or ""}
                continue
            if turn is None or not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    sig = _signature(block)
                    turn["tools"].append(sig)
                    turn["steps"] += steps(block)
                    turn["route"] += block_phases(block)
                    if sig.startswith("Skill: "):
                        turn["skills"].append(sig[7:])
                    elif sig.startswith("Agent: "):
                        turn["agents"].append(sig[7:])
            usage = msg.get("usage")
            if isinstance(usage, dict):  # one request is written as several lines that repeat its usage
                total = sum(v for k in ("output_tokens", "input_tokens", "cache_creation_input_tokens")
                            if type(v := usage.get(k)) is int)
                key = str(d.get("requestId") or msg.get("id") or d.get("uuid"))
                turn["usage"][key] = max(total, turn["usage"].get(key, 0))
    _end(turn, out)
    return out


def _agent_type(path) -> str:
    """The type of the sub-agent whose transcript this is, from the meta file Claude Code writes beside it; "" when unknown."""
    try:
        data = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    kind = data.get("agentType") if isinstance(data, dict) else None
    return kind if isinstance(kind, str) and 0 < len(kind) <= 40 and store.AGENT.match(kind) else ""


def read_turns(days=30, cfg=None, dirs=None, now=None) -> list:
    now = time.time() if now is None else now
    since = now - days * 86400
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(since))
    turns, seen = [], set()
    skips = config.settings(cfg)["skip_folders"]
    for base in config_dirs(cfg) if dirs is None else dirs:
        try:
            folders = sorted((base / "projects").iterdir())
        except OSError:
            continue
        for folder in folders:
            if not folder.is_dir() or any(skip in folder.name for skip in skips):
                continue
            try:  # a sub-agent's transcript is <session>/subagents/agent-<id>.jsonl, its type in agent-<id>.meta.json beside it
                files = [(p, None) for p in sorted(folder.glob("*.jsonl"))] + [(p, _agent_type(p)) for p in sorted(folder.glob("*/subagents/*.jsonl"))]
            except OSError:
                continue
            for path, kind in files:
                if kind == "":
                    continue  # a sub-agent of unknown type: a playbook for the main thread must not come from its work
                try:
                    if path.stat().st_mtime < since:
                        continue
                except OSError:
                    continue
                for turn in read_file(path, folder.name, cutoff, kind):
                    key = (turn["at"], turn["text"])  # a resumed or moved session is copied into another file, and keeps its time stamps
                    if key not in seen:
                        seen.add(key)
                        turns.append(turn)
    return turns


# ---- clustering -----------------------------------------------------------------------------------

def words(text) -> frozenset:
    return frozenset(w for w in re.findall(r"[a-z]{3,}", _DROP.sub(" ", text.lower())) if w not in STOP)


def cluster(turns) -> list:
    """Group turns greedily: a turn joins the first-best group whose seed it resembles (Jaccard of word sets)."""
    groups, seeds, index = [], [], {}
    for turn in turns:
        ws = words(turn["text"])
        if len(ws) < 3:  # "keep going" is not a task
            continue
        shared = Counter(g for w in ws for g in index.get(w, ()))
        best, top = None, SIMILAR
        for g, n in sorted(shared.items()):
            jac = n / (len(ws) + len(seeds[g]) - n)
            if jac >= top and (best is None or jac > top):
                best, top = g, jac
        if best is None:
            best = len(groups)
            groups.append([])
            seeds.append(ws)
            for w in ws:
                index.setdefault(w, []).append(best)
        groups[best].append(turn)
    return groups


def _matched(members) -> bool:
    """True when an existing playbook's triggers already match most of these requests."""
    books = store.listing()
    return any(sum(1 for t in members if offer.score(pb, t["text"]) > 0) * 2 >= len(members) for pb in books)


def _common(members, key="tools", most=12) -> list:
    """What most of the turns have under `key`, in the order it usually first appears."""
    seen, first = Counter(), {}
    for t in members:
        for pos, sig in enumerate(dict.fromkeys(t[key])):
            seen[sig] += 1
            first.setdefault(sig, []).append(pos)
    keep = [s for s, n in seen.items() if n >= COMMON * len(members)]
    return sorted(keep, key=lambda s: (statistics.median(first[s]), s))[:most]


def mechanical(route) -> bool:
    """True when the route is at least two commands and nothing else: only run.py can carry out a command. A skill is
    instructions for the model, and an edit, a read, an agent or any other tool needs judgment."""
    return len(route) >= 2 and all(s.startswith("Bash: ") for s in route)


def _phases(turn) -> list:
    return [p for p in map(phase, turn.get("steps", ())) if p]


def workflows(turns) -> list:
    """Group turns by the way the work was done, whatever the words: (phases of the seed, turns) pairs.

    The commonest sets of phases seed the groups, and a turn joins the seed its own set resembles most.
    Reading, searching and one-off scripts are in every turn, so they have no phase and do not count.
    """
    shaped = [(frozenset(_phases(t)), t) for t in turns]
    shaped = [(s, t) for s, t in shaped if len(s) >= MIN_PHASES]
    counts = Counter(s for s, _ in shaped)
    seeds, home = [], {}
    for s in sorted(counts, key=lambda s: (-counts[s], sorted(s))):
        near = max((x for x in seeds if len(s & x) >= MIN_PHASES), key=lambda seed: (len(s & seed) / len(s | seed), sorted(seed)), default=None)
        if near is None or len(s & near) / len(s | near) < SHAPE_SIMILAR:
            seeds.append(s)
            near = s
        home[s] = near
    groups = {seed: [] for seed in seeds}
    for s, t in shaped:
        groups[home[s]].append(t)
    return list(groups.items())


def describe_workflow(seed, members) -> dict:
    found = describe(members)
    for t in members:
        t["phases"] = _phases(t)
    seen = Counter(c for t in members for c in set(t.get("chains", ())))
    return dict(found, id=hashlib.sha256(("workflow:" + ",".join(_tagged(sorted(seed), found["agent_type"]))).encode()).hexdigest()[:12], kind="workflow",
                seed=sorted(seed), phases=_common(members, "phases"), steps=_common(members, "steps", 20),
                blocks=sorted(c for c, n in seen.items() if n >= COMMON * len(members)))


def _top(members, key) -> list:
    return [n for n, _ in Counter(x for t in members for x in set(t[key])).most_common(10)]


def describe(members) -> dict:
    texts = sorted(t["text"] for t in members)
    pool, chosen, sessions = sorted(members, key=lambda t: (-t["tokens"], t["text"])), [], set()
    for t in pool:  # examples from different sessions first
        if t["session"] not in sessions:
            sessions.add(t["session"])
            chosen.append(t)
    chosen += [t for t in pool if t not in chosen]
    return {"id": hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()[:12], "count": len(members),
            "sessions": len({t["session"] for t in members}),
            "projects": [p for p, _ in Counter(t["project"] for t in members).most_common(5)],
            "tokens": sum(t["tokens"] for t in members),
            "median_requests": statistics.median(t["requests"] for t in members),
            "examples": [t["text"] for t in chosen[:3]], "tools": _common(members), "agent_type": members[0].get("agent_type", ""),
            "skills": _top(members, "skills"), "agents": _top(members, "agents")}


def _tagged(seed, kind) -> list:
    """A set of phases kept apart by the agent type it was seen under: the main thread's has none."""
    return list(seed) + (["@" + kind] if kind else [])


def _stored() -> list:
    return [_tagged(pb["phases"], pb["agent_type"]) for pb in store.listing() if pb["phases"]]


def chain_id(kind, name) -> str:
    return hashlib.sha256(f"chain:{kind}:{name}".encode()).hexdigest()[:12]


def _building_blocks(turns, kind, enough, stored) -> list:
    """The small chains (test, read the failures, run again; wait until done; look at the git state before a commit) that recur
    across different workflows, or in a sub-agent's work, each as a candidate for a playbook of its own that the bigger
    playbooks then include instead of repeating its steps."""
    out = []
    for name, route in CHAIN_ROUTE.items():
        g = [t for t in turns if name in t.get("chains", ())]
        if enough(g) and (kind or len({frozenset(_phases(t)) for t in g}) >= 2) and not _overlaps(_tagged(route, kind), stored):
            out.append(dict(describe(g), id=chain_id(kind, name), kind="chain", chain=name, seed=route, phases=route,
                            steps=_common(g, "steps", 20)))
    return out


def find_candidates(turns, minimum=3) -> list:
    def enough(g):
        return len(g) >= minimum and len({t["session"] for t in g}) >= 2

    found, stored = [], _stored()
    for kind in dict.fromkeys(t.get("agent_type", "") for t in turns):  # the main thread, and each sub-agent type, on its own
        part = [t for t in turns if t.get("agent_type", "") == kind]
        alike = [g for g in cluster(part) if enough(g)]
        found += [describe(g) for g in alike if not _matched(g)]
        worded = [{id(t) for t in g} for g in alike]
        found += _building_blocks(part, kind, enough, stored)
        for seed, g in workflows(part):  # a way of working that is mostly one worded request again adds nothing
            if enough(g) and not _matched(g) and not _overlaps(_tagged(seed, kind), stored) and not any(len(ids & {id(t) for t in g}) * 2 > len(g) for ids in worded):
                found.append(describe_workflow(seed, g))
    return sorted(found, key=lambda c: (-c["tokens"], c["id"]))[:KEEP]


def mine(days=30, minimum=3, cfg=None, dirs=None, out=print, now=None) -> list:
    found = find_candidates(read_turns(days, cfg, dirs, now), minimum)
    for feed in FEEDS:  # a host's own sources of candidates; one that fails costs its candidates, not the transcript ones
        try:
            found = (found + feed(found, cfg))[:KEEP]
        except Exception:
            pass
    _save(candidates_path(), {"mined": int(time.time() if now is None else now), "days": days, "min": minimum, "candidates": found})
    if not found:
        out("no repeated multi-step requests found")
    else:
        out(f"{'COUNT':>5} {'SESS':>4} {'TOKENS':>9}  {'ID':<12} FIRST EXAMPLE, OR THE WAY OF WORKING")
    for c in found:
        what = "way of working: " + " > ".join(c["phases"]) if c.get("kind") == "workflow" else c["examples"][0]
        out(f"{c['count']:>5} {c['sessions']:>4} {c['tokens']:>9}  {c['id']:<12} {what[:70]}")
    return found



def _state() -> dict:
    data = _load(state_path(), {})
    for key in ("compiled", "tried", "runs"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    seeds = data.get("seeds")
    data["seeds"] = [x for x in seeds if isinstance(x, list) and all(isinstance(y, str) for y in x)] if isinstance(seeds, list) else []
    return data


def _today(now=None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))


def runs_today(now=None) -> int:
    n = _state()["runs"].get(_today(now), 0)
    return n if type(n) is int else 0


def _overlaps(seed, others) -> bool:
    seed = set(seed)
    return any(seed | o and len(seed & o) / len(seed | o) >= SHAPE_SIMILAR for o in map(set, others))


def _done(candidate, state) -> bool:
    """A way of working whose phases mostly match one already compiled, or one a stored playbook (any source) already does.

    Its id may have drifted, the work is the same."""
    seed = candidate.get("seed")
    if candidate.get("kind") not in ("workflow", "chain") or not isinstance(seed, list):
        return False
    seed = _tagged(seed, candidate.get("agent_type", ""))
    return _overlaps(seed, state["seeds"]) or _overlaps(seed, _stored())


def next_candidate(now=None):
    """The best saved candidate not compiled yet and not tried within RETRY seconds, or None."""
    state, now = _state(), time.time() if now is None else now
    found = _load(candidates_path(), {}).get("candidates") or []
    for c in sorted(found, key=lambda c: c.get("kind") != "chain"):  # the building blocks first: the workflows that include them come after
        tried = state["tried"].get(c.get("id"), 0)
        if c.get("id") not in state["compiled"] and not (type(tried) in (int, float) and now - tried < RETRY) and not _done(c, state):
            return c
    return None
