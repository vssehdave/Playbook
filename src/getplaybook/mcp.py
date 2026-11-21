"""`playbook mcp`: playbooks as tools, over stdio (Model Context Protocol, JSON-RPC 2.0).

Claude Code starts this next to a session. Tools: playbook_find, playbook_run, playbook_save.
"""
import json
import os
import sys

from . import __version__, config, offer, run, store
from .hook import project_names

TOOLS = [
    {"name": "playbook_find",
     "description": "Find recorded playbooks (a way of doing a repeated task) that match some words. Returns the top 5 "
                    "with name, title, when it applies and its status. Given a playbook's exact name, returns its steps.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string", "description": "The request, or words from it"}}}},
    {"name": "playbook_save",
     "description": "Record, or update, a playbook for a task that will come up again. A script is a draft until the "
                    "person approves it with `playbook approve NAME`.",
     "inputSchema": {"type": "object", "required": ["playbook"], "properties": {
         "playbook": {"type": "object", "description": "name (lowercase, digits, hyphens), title, when, triggers (2 to 5 short phrases), "
                                                       "steps (ordered \"If X: do Y with Z\" lines, at most 30), skills, agents, "
                                                       "args [{name, help}], project (optional)"},
         "script": {"type": "string", "description": "The text of run.py, if the job can be done with no model"}}}},
    {"name": "playbook_run",
     "description": "Run an approved playbook (the playbooks it includes first, then its own script) with no model in the loop. "
                    "Returns `RESULT: pass`, or `RESULT: fail (exit N)` with only what failed, and for a playbook of several steps "
                    "`RESUME: playbook_run {...}`: fix the cause, then call that to go on from the failed step. A draft script is not run.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {
         "name": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}},
         "from": {"type": "string", "description": "A step to start at (its name, or its number from 1), to go on after a failure"}}}},
]

INSTRUCTIONS = (
    "Playbooks are recorded ways of doing tasks that repeat: an ordered decision list and, where every step is mechanical, an approved "
    "script that does the job with no model. When a playbook is offered, follow it; call playbook_run for one with a script. "
    "After a multi-step task that will come up again, record it with the playbook skill and playbook_save.")


def call(name, args, cwd) -> str:
    if name == "playbook_find":
        query = str(args.get("query") or "").strip()
        named = store.load(query) if store.valid_name(query) else None
        if named:  # asked for by name: the whole playbook, nothing cut
            return offer.full(named)
        found = offer.find(query, project_names(cwd))
        return ("\n".join(f"{pb['name']}: {pb['title']} ({pb['status']}, used {pb['uses']}x)\n    {pb['when']}" for pb in found)
                or "No playbook matches.")
    if name == "playbook_save":
        given = args.get("playbook")
        if not isinstance(given, dict):
            raise ValueError("playbook must be an object")
        pb = store.save(dict(given, source="claude"), args.get("script"))
        text = f"Saved playbook {pb['name']} at {store.folder(pb['name'])} (status: {pb['status']})."
        if pb["status"] == "draft" and pb["script"]:
            text += (f" Its script is a draft and will not run through playbook_run. Tell the person to read "
                     f"{store.script_path(pb['name'])} and approve it with `{config.CLI} approve {pb['name']}`.")
        elif pb["status"] == "draft":
            text += f" It is a draft and is not offered until the person approves it with `{config.CLI} approve {pb['name']}`."
        return text
    if name == "playbook_run":
        return run.run(str(args.get("name") or ""), args.get("args") or [], cwd, start=args.get("from"))
    raise KeyError(name)


def listed(plugin=False) -> list:
    """The tools; none when turned off, or for the plugin's server under a host that lists these tools itself (no duplicates)."""
    return TOOLS if config.settings()["enabled"] and not (plugin and config.hosted()) else []


def handle(message, cwd, plugin=False):
    """Answer one JSON-RPC message. Returns the reply, or None for a notification."""
    method, ident = message.get("method"), message.get("id")
    if ident is None:
        return None
    if method == "initialize":
        asked = (message.get("params") or {}).get("protocolVersion") or "2024-11-05"
        result = {"protocolVersion": asked, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "playbook", "version": __version__}, "instructions": INSTRUCTIONS}
    elif method == "tools/list":
        result = {"tools": listed(plugin)}
    elif method == "tools/call":
        params = message.get("params") or {}
        try:
            text, failed = call(params.get("name"), params.get("arguments") or {}, cwd), False
        except KeyError:
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": f"unknown tool {params.get('name')}"}}
        except Exception as exc:  # a bad argument or a locked file: say so, keep serving
            text, failed = f"playbook error: {type(exc).__name__}: {exc}", True
        result = {"content": [{"type": "text", "text": text}], "isError": failed}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def serve(stdin=None, stdout=None, plugin=False) -> int:
    stdin, stdout = stdin or sys.stdin.buffer, stdout or sys.stdout.buffer
    cwd = os.getcwd()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(message, cwd, plugin) if isinstance(message, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()
    return 0


def client_config(extra_env=None) -> dict:
    """The `--mcp-config` entry that makes Claude Code start this server."""
    env = {"PYTHONIOENCODING": "utf-8", **(extra_env or {})}
    return {"mcpServers": {"playbook": {"command": sys.executable, "args": ["-m", "getplaybook", "mcp"], "env": env}}}
