import io
import json
import unittest
from pathlib import Path

from helpers import playbook
from test_store import SCRIPT, Home, make

from getplaybook import mcp


def rpc(*messages):
    stdin = io.BytesIO("".join(json.dumps(m) + "\n" for m in messages).encode())
    stdout = io.BytesIO()
    mcp.serve(stdin, stdout)
    return [json.loads(x) for x in stdout.getvalue().decode().splitlines()]


class McpTest(Home):
    def test_the_tool_list_is_find_run_and_save(self):
        replies = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                      {"jsonrpc": "2.0", "method": "notifications/initialized"},
                      {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, {"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(len(replies), 3)  # a notification has no answer
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "playbook")
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-06-18")
        tools = {t["name"]: t for t in replies[1]["result"]["tools"]}
        self.assertEqual(sorted(tools), ["playbook_find", "playbook_run", "playbook_save"])
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

    def test_turned_off_there_are_no_tools(self):
        (Path(self.home) / "config.json").write_text(json.dumps({"playbooks": {"enabled": False}}), encoding="utf-8")
        self.assertEqual(mcp.listed(), [])

    def test_the_plugin_declares_the_server_and_the_hooks(self):
        root = Path(__file__).parent.parent
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["mcpServers"]["playbook"]["args"], ["mcp"])
        hooks = json.loads((root / plugin["hooks"][0]).read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(sorted(hooks), ["PostToolUse", "PreToolUse", "UserPromptSubmit"])
        self.assertEqual(hooks["PostToolUse"][0]["hooks"][0]["command"], "playbook hook post-tool-use")


if __name__ == "__main__":
    unittest.main()
