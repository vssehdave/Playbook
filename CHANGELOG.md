# Changelog

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
