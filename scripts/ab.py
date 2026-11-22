#!/usr/bin/env python3
"""A/B harness: does Playbook help? Real headless Claude Code runs (Haiku) in the Almanac sandbox, playbooks off and on.

    python scripts/ab.py phase1 [--runs 3] [--tasks a,b,c,d,e] [--jobs 3] [--out DIR]   the matrix, then the table
    python scripts/ab.py phase2 [--sessions s1,s2,s3,s4] [--out DIR]                    multi-turn sessions, turn by turn
    python scripts/ab.py report DIR                                                     print the table from saved results

Needs `almanac` on PATH and a logged-in ~/.claude-sandbox. Every run gets a fresh copy of a throwaway clone of this repo at a fixed
commit and a fresh throwaway PLAYBOOK_HOME, so runs cannot affect each other or the real repo. Playbook writes no offer log of its own:
offers, takes and denies are read from the stream (`--include-hook-events` gives each hook's output).
Stdlib only.
"""
import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "src" / "getplaybook" / "plugin"
FIXED = "0496611"  # the commit every task repo is at
PLAYBOOK_SRC = Path.home() / ".almanac" / "playbooks"
PLAYBOOKS = ("git-state-and-test-run", "delegate-find-code", "delegate-review-branch", "delegate-build-change")
RUN_TIMEOUT = 600  # seconds before a run is killed
TOOLS = "Bash Read Grep Glob Edit Write Agent mcp__plugin_playbook_playbook"
# the sandbox has none of the owner's agents; the delegate playbooks name these three
AGENTS = {
    "explorer": {"description": "Read-only code search", "prompt": "You search code and answer briefly with path:line.",
                 "tools": ["Read", "Grep", "Glob"], "model": "haiku"},
    "builder": {"description": "Builds a change on a branch", "prompt": "You implement the task, run the tests and commit.", "model": "haiku"},
    "crew:code-reviewer": {"description": "Reviews a diff", "prompt": "You review a diff for bugs and report them briefly.",
                           "tools": ["Read", "Grep", "Glob", "Bash"], "model": "haiku"},
}
NOTE = re.compile(r'playbook "([\w-]+)"')


# ---- fixtures ------------------------------------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def build_template(dst: Path) -> Path:
    """A clone of this repo at FIXED on `main`, no remote, plus branch feat/strong-trigger: one commit that makes `strong` accept
    one-word triggers (a real bug, for the review task)."""
    subprocess.run(["git", "clone", "-q", str(ROOT), str(dst)], check=True)
    _git(dst, "checkout", "-q", "-B", "main", FIXED)
    _git(dst, "remote", "remove", "origin")
    for key, val in (("user.name", "AB Harness"), ("user.email", "ab@example.invalid"), ("core.autocrlf", "false")):
        _git(dst, "config", key, val)
    _git(dst, "checkout", "-q", "-b", "feat/strong-trigger")
    path = dst / "src" / "getplaybook" / "offer.py"
    text = path.read_text(encoding="utf-8")
    assert "len(w) >= 2" in text
    path.write_text(text.replace("len(w) >= 2", "len(w) >= 1"), encoding="utf-8")
    _git(dst, "commit", "-qam", "feat(offer): start scripts on short triggers")
    _git(dst, "checkout", "-q", "main")
    return dst


def make_home(dst: Path, arm: str) -> Path:
    """OFF: empty home. ON: the four playbooks approved in suggest mode; ENF: the same, the test playbook in enforce mode.
    The test playbook is mined with agent_type "general-purpose", which keeps it off the main thread: it is set to "" here."""
    dst.mkdir(parents=True)
    if arm == "off":
        return dst
    for name in PLAYBOOKS:
        shutil.copytree(PLAYBOOK_SRC / name, dst / "playbooks" / name)
    path = dst / "playbooks" / "git-state-and-test-run" / "playbook.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["agent_type"] = ""
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    env = dict(os.environ, PLAYBOOK_HOME=str(dst), PYTHONPATH=str(ROOT / "src"))
    for name in PLAYBOOKS:
        mode = "enforce" if arm == "enf" and name == "git-state-and-test-run" else "suggest"
        subprocess.run([sys.executable, "-m", "getplaybook", "approve", name, "--mode", mode], env=env, check=True, capture_output=True)
    return dst


# ---- running Claude ------------------------------------------------------------------------------

def run_claude(prompt, cwd, home, resume=None, timeout=RUN_TIMEOUT) -> tuple:
    """One headless turn. Returns (events, wall seconds); each event is a stream line plus `_t`, its arrival time in seconds."""
    cmd = [shutil.which("almanac") or "almanac", "sandbox", "--plugin", str(PLUGIN), "--env", f"PLAYBOOK_HOME={home}", "--",
           "-p", prompt, "--output-format", "stream-json", "--verbose", "--include-hook-events", "--model", "haiku",
           "--allowedTools", TOOLS, "--agents", json.dumps(AGENTS)] + (["--resume", resume] if resume else [])
    start = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def kill():
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True) if os.name == "nt" else proc.kill()

    timer = threading.Timer(timeout, kill)
    timer.start()
    events = []
    for raw in proc.stdout:
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        event["_t"] = time.monotonic() - start
        events.append(event)
    proc.wait()
    timer.cancel()
    return events, time.monotonic() - start


# ---- parsing (pure: the unit test feeds it fake events) ---------------------------------------------

def blocks(event) -> list:
    content = (event.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(str(b.get("text") or "") for b in content or () if isinstance(b, dict))


def parse(events) -> dict:
    """Everything one turn tells us. Tokens, cost and turns come from the final `result` event; offers, takes and denies from the
    hook outputs and the tool calls."""
    out = {"ok_run": False, "final": "", "cost": 0.0, "in": 0, "out": 0, "cache_read": 0, "cache_write": 0, "turns": 0, "session": None,
           "tools": 0, "sub_tools": 0, "tool_names": {}, "offers": [], "runs": [], "run_results": [], "denies": 0, "recovered": False,
           "agent_calls": 0, "agent_unchanged": None, "errors": 0, "hook_ms": {}}
    uses, started, call, deny_at, run_at = {}, {}, None, None, []
    for n, ev in enumerate(events):
        kind = ev.get("type")
        if ev.get("session_id"):
            out["session"] = ev["session_id"]
        if kind == "result":
            out["ok_run"] = ev.get("subtype") == "success" and not ev.get("is_error")
            out["final"], out["cost"], out["turns"] = ev.get("result") or "", ev.get("total_cost_usd") or 0.0, ev.get("num_turns") or 0
            usage = list((ev.get("modelUsage") or {}).values())
            for key, names in (("in", ("inputTokens",)), ("out", ("outputTokens",)), ("cache_read", ("cacheReadInputTokens",)),
                               ("cache_write", ("cacheCreationInputTokens",))):
                out[key] = sum(u.get(names[0]) or 0 for u in usage)
        elif kind == "assistant":
            for b in blocks(ev):
                if b.get("type") != "tool_use":
                    continue
                uses[b["id"]] = b
                out["sub_tools" if ev.get("parent_tool_use_id") else "tools"] += 1
                name = b.get("name", "")
                out["tool_names"][name] = out["tool_names"].get(name, 0) + 1
                if name.endswith("playbook_run"):
                    out["runs"].append((b.get("input") or {}).get("name"))
                    run_at.append(n)
                if name in ("Agent", "Task") and not ev.get("parent_tool_use_id"):
                    out["agent_calls"] += 1
                    if call is not None:
                        given = b.get("input") or {}
                        same = given.get("prompt") == call.get("prompt") and given.get("subagent_type") == call.get("subagent_type")
                        out["agent_unchanged"] = same if out["agent_unchanged"] is None else out["agent_unchanged"] and same
        elif kind == "user":
            for b in blocks(ev):
                if b.get("type") != "tool_result":
                    continue
                out["errors"] += bool(b.get("is_error"))
                text = text_of(b.get("content"))
                if (uses.get(b.get("tool_use_id")) or {}).get("name", "").endswith("playbook_run"):
                    out["run_results"].append(text.split("\n", 1)[0][:60])
                    found = re.search(r"AGENT CALL: (\{.*\})", text)
                    if found:
                        call = json.loads(found.group(1))
        elif kind == "system" and ev.get("subtype") == "hook_started":
            started[ev.get("hook_id")] = ev["_t"]
        elif kind == "system" and ev.get("subtype") == "hook_response":
            if ev.get("hook_id") in started:
                out["hook_ms"].setdefault(ev.get("hook_event"), []).append(round(1000 * (ev["_t"] - started[ev["hook_id"]])))
            raw = ev.get("stdout") or ev.get("output") or ""
            try:
                spec = json.loads(raw)["hookSpecificOutput"] if raw.strip() else {}
            except (ValueError, KeyError, TypeError):
                spec = {}
            if spec.get("permissionDecision") == "deny":
                out["denies"] += 1
                deny_at = deny_at if deny_at is not None else n
            elif NOTE.search(spec.get("additionalContext") or ""):
                out["offers"].append((spec.get("hookEventName"), NOTE.search(spec["additionalContext"]).group(1)))
    out["recovered"] = deny_at is not None and any(r > deny_at for r in run_at)
    return out


def derived(run) -> dict:
    """Numbers one run adds up to: offers made, offers taken (playbook_run of an offered name), repeated offers."""
    names = [n for _, n in run["offers"]]
    return {"offers": len(names), "takes": len({n for n in run["runs"] if n in names}), "repeats": len(names) - len(set(names))}


# ---- aggregation ---------------------------------------------------------------------------------

def spread(xs):
    return max(xs) - min(xs)


def real(a, b) -> bool:
    """A difference is real only when the means differ by more than the larger within-arm spread."""
    return len(a) > 1 and len(b) > 1 and abs(statistics.mean(a) - statistics.mean(b)) > max(spread(a), spread(b))


METRICS = (("ok", "ok"), ("cost", "cost$"), ("tok", "in+out"), ("cache_read", "c.read"), ("cache_write", "c.write"), ("calls", "calls"),
           ("turns", "turns"), ("wall", "wall s"), ("offers", "offers"), ("takes", "takes"))


def row(r) -> dict:
    return {"ok": float(bool(r["ok"])), "cost": r["cost"], "tok": r["in"] + r["out"], "cache_read": r["cache_read"], "cache_write": r["cache_write"],
            "calls": r["tools"] + r["sub_tools"], "turns": r["turns"], "wall": r["wall"], "offers": r["offers_n"], "takes": r["takes"]}


def _cell(xs, key) -> str:
    big = key in ("tok", "cache_read", "cache_write")
    fmt = (lambda v: f"{v:,.0f}") if big or key in ("calls", "turns", "wall", "offers", "takes") else (lambda v: f"{v:.3f}" if key == "cost" else f"{v:.2f}")
    return f"{fmt(statistics.mean(xs))} [{fmt(min(xs))}-{fmt(max(xs))}]"


def table(results) -> str:
    """Per task and arm: mean [min-max] of every metric over the runs; a `*` on the on-arm cell when it differs from off by more than
    the spread of either arm."""
    lines = []
    for task in sorted({r["task"] for r in results}):
        lines.append(f"\n== task {task} ==")
        lines.append(f"{'arm':4} {'n':>2} " + " ".join(f"{label:>22}" for _, label in METRICS))
        arms = {a: [row(r) for r in results if r["task"] == task and r["arm"] == a] for a in ("off", "on", "enf")}
        for arm, rows in arms.items():
            if not rows:
                continue
            cells = []
            for key, _ in METRICS:
                xs = [x[key] for x in rows]
                star = "*" if arm != "off" and arms["off"] and key not in ("offers", "takes", "ok") and real([x[key] for x in arms["off"]], xs) else ""
                cells.append(f"{_cell(xs, key) + star:>22}")
            lines.append(f"{arm:4} {len(rows):>2} " + " ".join(cells))
    return "\n".join(lines) + "\n\n* = means differ by more than the larger within-arm spread (max-min); unmarked differences are noise."


# ---- tasks ---------------------------------------------------------------------------------------

def _tests_pass(repo) -> bool:
    env = dict(os.environ, PYTHONPATH="src")
    return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=repo, env=env, capture_output=True).returncode == 0


def check_d(final, repo) -> tuple:
    try:
        src = _git(repo, "show", "fix/min-request:src/getplaybook/offer.py")
        ahead = int(_git(repo, "rev-list", "--count", "main..fix/min-request").strip())
        tests = _git(repo, "diff", "--name-only", "main", "fix/min-request", "--", "tests").strip()
    except subprocess.CalledProcessError:
        return False, "branch fix/min-request missing"
    if not re.search(r"(?m)^MIN_REQUEST = 10\b", src):
        return False, "MIN_REQUEST not 10"
    if not ahead or not tests:
        return False, "no commit or no test change"
    _git(repo, "checkout", "-q", "fix/min-request")
    return (True, "ok") if _tests_pass(repo) else (False, "tests fail on the branch")


TASKS = {
    "a": ("git state + tests", "run the tests and check git status for this repo, then tell me the result in two lines.",
          lambda f, r: ("170" in f and "main" in f.lower(), "needs 170 tests and main")),
    "b": ("review branch", "review the branch feat/strong-trigger against main in {repo}. List the bugs you find, most serious first.",
          lambda f, r: (bool(re.search(r"strong", f, re.I)) and bool(re.search(r"single|one-word|one word|>= ?1|len\(w\)|short|auto|weak|two", f, re.I)),
                        "must name strong() and the one-word trigger")),
    "c": ("find offers decided", "find where offers are decided in this repo and tell me the file and function.",
          lambda f, r: ("offer.py" in f and "decide" in f, "needs offer.py and decide")),
    "d": ("small fix on branch", "make the change on a branch fix/min-request: set MIN_REQUEST in src/getplaybook/offer.py from 12 to 10 and add a "
          "test for it in tests/test_offer.py. Run the tests, commit, do not push.", check_d),
    "e": ("control", "which python versions does this project support? answer in one line from pyproject.toml.",
          lambda f, r: ("3.10" in f, "needs 3.10")),
}


def one_run(job) -> dict:
    task, arm, n, work, template = job
    base = work / f"{task}-{arm}-{n}"
    repo = base / "repo"
    shutil.copytree(template, repo)
    home = make_home(base / "home", arm)
    name, prompt, check = TASKS[task]
    events, wall = run_claude(prompt.format(repo=repo), repo, home)
    got = parse(events)
    ok, why = check(got["final"], repo) if got["ok_run"] else (False, "run did not finish")
    rec = {"task": task, "arm": arm, "run": n, "ok": ok, "why": why, "wall": wall, **got, **{"offers_n": 0, "takes": 0, "repeats": 0}}
    d = derived(got)
    rec.update(offers_n=d["offers"], takes=d["takes"], repeats=d["repeats"])
    rec["raw_head"] = got["final"][:300]
    print(f"  {task}-{arm}-{n}: ok={ok} ({why}) ${got['cost']:.3f} {wall:.0f}s offers={d['offers']} takes={d['takes']} denies={got['denies']}", flush=True)
    return rec


def phase1(args):
    work = Path(tempfile.mkdtemp(prefix="pbab-"))
    template = build_template(work / "template")
    tasks = [t for t in args.tasks.split(",") if t]
    jobs = []
    for n in range(1, args.runs + 1):  # interleaved, so machine load falls on both arms alike
        for t in tasks:
            for arm in ("off", "on") + (("enf",) if t in ("a", "d") else ()):
                jobs.append((t, arm, n, work, template))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    try:
        with ThreadPoolExecutor(args.jobs) as pool:
            for rec in pool.map(one_run, jobs):
                results.append(rec)
                (out / "phase1.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(table(results))


# ---- phase 2 -------------------------------------------------------------------------------------

REPO = "{repo}"
SESSIONS = {
    "s1": ("on", [
        ("What does decide() in src/getplaybook/offer.py do? Two sentences.", set()),
        ("run the tests and check git status for this repo, tell me the result", {"git-state-and-test-run"}),
        (f"review the branch feat/strong-trigger against main in {REPO} and list the bugs", {"delegate-review-branch"}),
        ("unrelated: write one line about why caching is hard.", set()),
        ("run the tests and check git status again please", {"git-state-and-test-run"}),
        ("thanks. which python versions does the project support? one line.", set())]),
    "s2": ("enf", [
        ("run the tests and check git status for this repo, tell me the result", {"git-state-and-test-run"}),
        ("now run python -m unittest discover -s tests directly and give only the count", set()),
        ("find where the mode of a playbook is decided and tell me file and function", {"delegate-find-code"}),
        ("ok, run the tests once more and tell me if anything changed", {"git-state-and-test-run"}),
        ("tell me a short joke", set())]),
    "s3": ("on", [
        ("make the change on a branch fix/min-request: set MIN_REQUEST in src/getplaybook/offer.py to 10 and add a test for it. Tests must pass; commit, do not push.",
         {"delegate-build-change"}),
        (f"review the branch fix/min-request against main in {REPO}, list bugs", {"delegate-review-branch"}),
        ("check git status", {"git-state-and-test-run"}),
        ("what does the last commit message say? use git log", set()),
        ("run the tests", {"git-state-and-test-run"}),
        ("summarise what we did in three bullets", set())]),
    "s4": ("on", [
        ("Read src/getplaybook/store.py and tell me how many functions it defines.", set()),
        ("Read src/getplaybook/hook.py and name the three hook events.", set()),
        ("Read tests/test_mine.py and tell me how many test classes it has.", set()),
        ("Read src/getplaybook/mine.py and tell me what it mines.", set()),
        ("Read src/getplaybook/build.py and say what the background worker does.", set()),
        ("Read src/getplaybook/cli.py and list its commands.", set()),
        ("Read tests/test_store.py and say what it covers.", set()),
        ("Read src/getplaybook/steps.py and say what a phase is.", set()),
        ("run the tests and check git status for this repo", {"git-state-and-test-run"}),
        ("Read src/getplaybook/run.py and say what RESUME does.", set())]),
}


def phase2(args):
    work = Path(tempfile.mkdtemp(prefix="pbab2-"))
    template = build_template(work / "template")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    try:
        for key in [s for s in args.sessions.split(",") if s]:
            arm, turns = SESSIONS[key]
            repo = work / key / "repo"
            shutil.copytree(template, repo)
            home = make_home(work / key / "home", arm)
            sid, rows = None, []
            print(f"session {key} ({arm})", flush=True)
            for i, (prompt, expect) in enumerate(turns, 1):
                events, wall = run_claude(prompt.replace(REPO, str(repo)), repo, home, resume=sid)
                got = parse(events)
                sid = got["session"] or sid
                offered = sorted({n for _, n in got["offers"]})
                row_ = {"turn": i, "prompt": prompt[:70], "expected": sorted(expect), "offered": offered, "offer_events": got["offers"],
                        "right_offers": offered == sorted(expect), "runs": got["runs"], "run_results": got["run_results"],
                        "denies": got["denies"], "recovered": got["recovered"], "tools": got["tool_names"], "agent_calls": got["agent_calls"],
                        "agent_unchanged": got["agent_unchanged"], "hook_ms": got["hook_ms"], "wall": round(wall, 1), "cost": round(got["cost"], 4),
                        "turns": got["turns"], "final": got["final"][:240], "ok_run": got["ok_run"], **derived(got)}
                rows.append(row_)
                print(f"  t{i}: offered={offered} expected={sorted(expect)} runs={got['runs']} denies={got['denies']} rec={got['recovered']} "
                      f"agent_unchanged={got['agent_unchanged']} hook_ms={got['hook_ms']} {wall:.0f}s ${got['cost']:.3f}", flush=True)
                report[key] = rows
                (out / "phase2.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("phase1")
    p1.add_argument("--runs", type=int, default=3)
    p1.add_argument("--tasks", default="a,b,c,d,e")
    p1.add_argument("--jobs", type=int, default=3)
    p1.add_argument("--out", default=str(ROOT / "scripts" / "ab-results"))
    p2 = sub.add_parser("phase2")
    p2.add_argument("--sessions", default="s1,s2,s3,s4")
    p2.add_argument("--out", default=str(ROOT / "scripts" / "ab-results"))
    rp = sub.add_parser("report")
    rp.add_argument("dir")
    args = ap.parse_args(argv)
    if args.cmd == "phase1":
        phase1(args)
    elif args.cmd == "phase2":
        phase2(args)
    else:
        print(table(json.loads((Path(args.dir) / "phase1.json").read_text(encoding="utf-8"))))


if __name__ == "__main__":
    main()
