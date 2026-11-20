"""The `playbook` command."""
import argparse
import sys

from . import config, hook, run, store

USAGE = """playbook: recorded ways of doing repeated tasks, and their scripts

  playbook list|show NAME|approve NAME [--auto]|remove NAME|path
  playbook run NAME [ARGS...] [--from STEP]   run an approved playbook (its includes first), as the playbook_run tool does
  playbook mine [--days 30] [--min 3]         find tasks you repeat in past sessions, no model
  playbook compile [--top N] [--dry-run]      have one Claude Code session write the best repeat up as a draft playbook
  playbook dedupe [--include-approved]        merge playbooks that do the same route into one
  playbook mcp                                the MCP server (playbook_find, playbook_run, playbook_save)
  playbook hook post-tool-use|user-prompt-submit   a Claude Code hook: reads its JSON on stdin
  playbook work                               the background run: mine, then compile within the daily cap
"""


def _print(text):
    """Print text that may hold characters the console cannot show."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode(sys.stdout.encoding or "ascii", "replace").decode(sys.stdout.encoding or "ascii"))


def _parser():
    parser = argparse.ArgumentParser(prog=config.CLI, usage=USAGE)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("list", "path", "work", "mcp"):
        sub.add_parser(name)
    mining = sub.add_parser("mine")
    mining.add_argument("--days", type=int, default=30)
    mining.add_argument("--min", type=int, default=3, dest="minimum")
    writing = sub.add_parser("compile")
    writing.add_argument("--top", type=int, default=1)
    writing.add_argument("--dry-run", action="store_true")
    sub.add_parser("dedupe").add_argument("--include-approved", action="store_true", help="also merge approved playbooks")
    for name in ("show", "approve", "remove"):
        one = sub.add_parser(name)
        one.add_argument("name")
        if name == "approve":
            one.add_argument("--auto", action="store_true", help="also let a host that supports it run the script before the model sees a matching request")
    running = sub.add_parser("run")
    running.add_argument("name")
    running.add_argument("args", nargs="*")
    running.add_argument("--from", dest="start", default=None, help="a step to start at (its name, or its number from 1)")
    sub.add_parser("hook").add_argument("event", choices=sorted(hook.EVENTS))
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    args = _parser().parse_args(argv)
    if args.cmd == "hook":
        text = hook.run(args.event, sys.stdin.buffer.read().decode("utf-8", "replace"))
        if text:
            sys.stdout.write(text + "\n")
        return 0
    if args.cmd == "mcp":
        from . import mcp

        return mcp.serve()
    if args.cmd in ("mine", "compile", "work"):
        from . import build, mine

        if args.cmd == "mine":
            mine.mine(args.days, args.minimum, out=_print)
            return 0
        if args.cmd == "work":
            return build.work()
        return build.compile_top(args.top, args.dry_run, out=_print)
    if args.cmd == "path":
        print(store.root())
        return 0
    if args.cmd == "dedupe":
        merged = store.dedupe(args.include_approved, out=_print)
        print(f"{merged} merged" if merged else "nothing to merge")
        return 0
    if args.cmd == "list":
        for pb in store.listing():
            print(f"{pb['name']:<28} {store.shown_status(pb):<22} {pb['uses']:>4}  {pb['title']}")
        return 0
    if args.cmd == "run":
        try:
            _print(run.run(args.name, args.args, None, start=args.start))
        except (ValueError, run.NotRun) as why:
            print(f"{config.CLI.split()[0]}: {why}", file=sys.stderr)
            return 2
        return 0
    pb = store.load(args.name) if store.valid_name(args.name) else None
    if pb is None:
        print(f"{config.CLI.split()[0]}: no usable playbook called {args.name!r}; see `{config.CLI} list`", file=sys.stderr)
        return 2
    if args.cmd == "show":
        print(f"{pb['name']}  ({store.shown_status(pb).replace(', auto', ', automatic')}, used {pb['uses']} times, from {pb['source']}, {pb['created']})\n{pb['title']}\n"
              f"When: {pb['when']}\nTriggers: {', '.join(pb['triggers'])}\nProject: {pb['project'] or 'any'}")
        print("Steps:\n" + "\n".join(f"  {i}. {s}" for i, s in enumerate(pb["steps"], 1)))
        print(f"Skills: {', '.join(pb['skills'])}\nAgents: {', '.join(pb['agents'])}")
        print(f"Route: {' > '.join(store.route_of(pb))}\nFor: {pb['agent_type'] or 'the main thread'}\nIncludes: {', '.join(pb['includes'])}")
        if pb["script"]:
            print(f"Script: {store.script_path(pb['name'])}  (hash {pb['hash'] or 'not approved'})")
        return 0
    if args.cmd == "remove":
        store.remove(args.name)
        print(f"removed {args.name}")
        return 0
    try:
        digest, text = store.approve(args.name, args.auto)
    except ValueError as why:
        print(f"{config.CLI.split()[0]}: {why}", file=sys.stderr)
        return 2
    if not pb["script"]:
        print(f"Approved {args.name}. It has no script; Claude is shown its steps when a request matches.")
        return 0
    lines = text.splitlines()
    _print("\n".join(lines[:40]) + (f"\n... ({len(lines) - 40} more lines)" if len(lines) > 40 else ""))
    print(f"\nApproved {args.name}. sha256 {digest}\nIf the script changes, it goes back to draft until you approve it again.")
    if args.auto:
        print(f"Automatic: a host that supports it runs this script before Claude sees a matching request, in the conversation's folder, "
              f"for up to {run.AUTO_TIMEOUT} seconds, with no model and no prompt. A script that runs the project's own tests or build runs "
              f"that repository's code. Approve again without --auto to stop that.")
    return 0
