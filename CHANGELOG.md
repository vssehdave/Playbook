# Changelog

## Unreleased

- Delegate playbooks: `playbook.json` may hold `delegate: {agent, prompt}`, a ready-made sub-agent call. `playbook_run`
  runs any scripts, then ends a passing report with `AGENT CALL: {...}` (the Agent tool input, with `{arg}` placeholders
  filled by position) for Claude to send as it is. The approved hash covers the delegate (unchanged for playbooks without
  one), `playbook_save` takes it, and `show`, the offer notes and the tool descriptions mention it.
- Offer modes, chosen when approving: `playbook approve NAME --mode suggest|enforce|auto` (`--auto` stays as
  `--mode auto`). The mode is signed with the command line's key in `auto.json` together with the script's hash, so
  editing `playbook.json` or the entry cannot change it; entries written before modes still read as `auto`.
  `list` and `show` print the mode.
- `enforce`: a new PreToolUse hook (`playbook hook pre-tool-use`) refuses a tool call that does a step of the route of a
  playbook offered in this turn, and tells Claude to call `playbook_run` instead. Reading, editing and the playbook tools
  are never refused, and once a run of that playbook does not pass in the turn, the steps are Claude's again. The
  `enforce` setting turns it off.
- `auto` in plain Claude Code: the UserPromptSubmit hook runs an automatic script itself when the request matches it
  strongly (setting `auto_run`) and gives Claude the result instead of the note. Its timeout is now 120 seconds.
- The plugin's MCP server (`playbook mcp --plugin`) lists no tools when `ALMANAC_PROXY` is set, since that host lists
  them itself and they showed twice.
- `sessions.json` is changed under a lock (`sessions.lock`, created exclusively, stale after 10 seconds), so two hooks at
  once no longer drop each other's entries; a hook that cannot get the lock within a second skips saving.
- `playbook setup` installs the plugin in one step: `pip install getplaybook && playbook setup`. The plugin files now
  live in the package (`src/getplaybook/plugin/`), so the wheel ships them; setup copies them to `~/.playbook/plugin`
  and runs `claude plugin marketplace add` and `claude plugin install`. The repository's marketplace points there.

## 0.1.0

First release. Playbook is the playbook feature of a Claude Code proxy, split out so it works on its own, as a Python
package (`getplaybook`, command `playbook`) and a Claude Code plugin.

- Playbooks: a folder with `playbook.json` (when it applies, triggers, an ordered decision list) and an optional `run.py`.
  A script runs only after the person approved its exact text (`playbook approve`); an edit sends it back to draft.
  `approve --auto` is signed with a key only the command line holds.
- Route offers: while Claude works, a playbook is offered when two of three signals agree: the phases of the tool calls so
  far, Claude's own narration, and whether the repo has something to ship.
- Nested layers: a playbook can include others; the runner runs them in order, stops at the first failure and prints
  `RESULT: pass` or `RESULT: fail (...)` and a `RESUME:` line to go on from the failed step.
- Sub-agent playbooks per agent type.
- Mining without a model: `playbook mine` reads Claude Code's own session files (read-only, sub-agent files too) and finds
  repeated requests, ways of working and building blocks (test loops, waits, git state checks); `playbook compile` has
  one capped headless Claude Code session write the best one up as a draft; `playbook dedupe` keeps one playbook per route.
- Plugin: the `playbook` skill, an MCP server (`playbook_find`, `playbook_run` with `from`, `playbook_save`), a PostToolUse
  hook that offers by route and a UserPromptSubmit hook that offers by words. Both hooks exit 0 silently on any error and
  do nothing when `ALMANAC_PROXY` is set, because that program places offers itself.
- Settings in `config.json` under `playbooks`, including `skip_folders` (session folders the miner leaves out).
