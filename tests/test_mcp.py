import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from helpers import playbook
from test_store import SCRIPT, Home, make

from getplaybook import mcp, register


def rpc(*messages):
    stdin = io.BytesIO("".join(json.dumps(m) + "\n" for m in messages).encode())
    stdout = io.BytesIO()
    mcp.serve(stdin, stdout)
    return [json.loads(x) for x in stdout.getvalue().decode().splitlines()]


class McpTest(Home):
    def test_the_tool_list_is_find_run_save_and_dedupe(self):
        replies = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                      {"jsonrpc": "2.0", "method": "notifications/initialized"},
                      {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, {"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(len(replies), 3)  # a notification has no answer
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "playbook")
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        tools = {t["name"]: t for t in replies[1]["result"]["tools"]}
        self.assertEqual(sorted(tools), ["playbook_dedupe", "playbook_find", "playbook_run", "playbook_save"])
        self.assertIn("from", tools["playbook_run"]["inputSchema"]["properties"])
        self.assertTrue(all(t["inputSchema"]["type"] == "object" for t in tools.values()))

    def test_a_call_round_trips_and_an_unknown_tool_or_method_is_an_error(self):
        playbook.save(make(), script=SCRIPT)
        playbook.approve("weekly-report")
        ok, unknown, method = rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "playbook_run", "arguments": {"name": "weekly-report", "args": ["a"]}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 3, "method": "no/such"})
        self.assertFalse(ok["result"]["isError"])
        self.assertTrue(ok["result"]["content"][0]["text"].startswith("RESULT: pass"))
        self.assertEqual(unknown["error"]["code"], -32602)
        self.assertEqual(method["error"]["code"], -32601)

    def test_run_goes_on_from_a_step(self):
        playbook.save(make("one-step", triggers=["one step"]), script="import sys\nsys.exit(3)\n")
        playbook.approve("one-step")
        playbook.save(make(triggers=["weekly report"], includes=["one-step"]), script=SCRIPT)
        playbook.approve("weekly-report")
        (failed,) = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "playbook_run", "arguments": {"name": "weekly-report"}}})
        self.assertIn("RESUME: playbook_run", failed["result"]["content"][0]["text"])
        (resumed,) = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "playbook_run", "arguments": {"name": "weekly-report", "from": "weekly-report"}}})
        self.assertTrue(resumed["result"]["content"][0]["text"].startswith("RESULT: pass"))

    def test_dedupe_says_what_it_merged_and_a_dry_run_changes_nothing(self):
        for name in ("ship-a", "ship-b"):
            playbook.save(make(name, triggers=[name.replace("-", " ")], source="claude", route=["commit", "push", "pr"]))

        def ask(**args):
            (reply,) = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "playbook_dedupe", "arguments": args}})
            return reply["result"]["content"][0]["text"]
        self.assertEqual(ask(dry_run=True), "would merge ship-b into ship-a")
        self.assertEqual(playbook.names(), ["ship-a", "ship-b"])
        self.assertEqual(ask(), "merged ship-b into ship-a")
        self.assertEqual(ask(), "nothing to merge")

    def test_turned_off_there_are_no_tools(self):
        (Path(self.home) / "config.json").write_text(json.dumps({"playbooks": {"enabled": False}}), encoding="utf-8")
        self.assertEqual(mcp.listed(), [])

    def test_the_plugin_server_lists_nothing_under_a_host_that_lists_the_tools(self):
        with mock.patch.dict(os.environ, {"ALMANAC_PROXY": "1"}):
            self.assertEqual(mcp.listed(plugin=True), [])
            self.assertEqual(len(mcp.listed()), 4)  # the host's own entry
        self.assertEqual(len(mcp.listed(plugin=True)), 4)

    def test_the_plugin_declares_the_server_and_the_hooks(self):
        root = Path(__file__).parent.parent / "src" / "getplaybook" / "plugin"
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["mcpServers"]["playbook"]["args"], ["mcp", "--plugin"])
        hooks = json.loads((root / plugin["hooks"][0]).read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(sorted(hooks), ["PostToolUse", "PreToolUse", "UserPromptSubmit"])
        self.assertEqual(hooks["PostToolUse"][0]["hooks"][0]["command"], "playbook hook post-tool-use")
        shipped = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        repo = json.loads((root.parent.parent.parent / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual((shipped["plugins"][0].pop("source"), repo["plugins"][0].pop("source")), ("./", "./src/getplaybook/plugin"))
        self.assertEqual(shipped, repo)  # the two differ only in where the plugin is

    def test_setup_installs_the_shipped_plugin_or_says_only_the_library_was(self):
        said = []
        with mock.patch("getplaybook.register.find_claude", return_value=None):
            self.assertEqual(register.setup(out=said.append), 0)
        self.assertIn("only the library", said[-1])
        calls = []

        def fake(cmd, **_):
            calls.append(cmd[1:])
            return mock.Mock(returncode=1 if "marketplace" in cmd else 0, stdout="", stderr="already added" if "marketplace" in cmd else "")

        with mock.patch("getplaybook.register.find_claude", return_value="claude"), mock.patch("subprocess.run", fake):
            self.assertEqual(register.setup(dry_run=True, out=said.append), 0)
            self.assertEqual(calls, [])
            self.assertEqual(register.setup(out=said.append), 0)  # a marketplace added before does not stop the install
        dest = Path(self.home) / "plugin"
        self.assertEqual(calls, [["plugin", "marketplace", "add", str(dest)], ["plugin", "install", "playbook@playbook"]])
        self.assertTrue((dest / ".claude-plugin" / "plugin.json").is_file() and (dest / "skills" / "playbook" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
