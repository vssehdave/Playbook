# Playbook: project brief

Playbook turns work Claude Code repeats into deterministic steps. It finds
repeats in Claude Code transcripts (main threads and sub-agents) without a
model, compiles them into playbooks (steps plus an approved, signed script),
and offers the right one while Claude works.

## How it is built

- Python package `getplaybook` (stdlib only, Python 3.10+), command `playbook`.
  Modules: `store` (folders, approval signing), `run` (runner: includes,
  `RESULT:`/`RESUME:`), `offer` (`offer.decide`, the route rule), `steps`,
  `mine`, `build` (compile, background worker), `text` (redaction),
  `thread`, `config`, `hook`, `mcp`, `cli`.
- Claude Code plugin inside the package, so the wheel ships it:
  `src/getplaybook/plugin/` (`.claude-plugin/plugin.json`, a one-plugin
  `marketplace.json` with source `./`, `hooks/playbook-hooks.json`,
  `skills/playbook/`); the repo's `.claude-plugin/marketplace.json` points
  there. `playbook setup` (module `register`) installs it. MCP tools: `playbook_find`, `playbook_run` (with
  `from`), `playbook_save`.
- Home: `PLAYBOOK_HOME` or `~/.playbook`.

## Core rules of the design

- Offers come from what Claude is doing, not prompt words. Two of three
  signals must agree: the route so far (tool calls), the model's own
  narration, and git facts. Prompt words matched 0 of 6 real tasks.
- Deterministic layers nest: playbooks `includes` other playbooks, inner
  ones trigger on their own, and a failed step returns `RESULT: fail` plus
  `RESUME: playbook_run {name, from}` so the model fills only the gap.
- A playbook with `agent_type` T is offered only to sub-agents of type T;
  `""` means the main thread.
- Scripts never run unless approved; approval is signed, so an edited
  script cannot run.

## Relation to Almanac

Almanac (the owner's private proxy) depends on this package and keeps only
proxy-side offer placement. It sets `ALMANAC_PROXY`, `PLAYBOOK_HOME` and
`PLAYBOOK_CONFIG` for the Claude Code it launches; the hooks stay silent
when `ALMANAC_PROXY` is set. Under Almanac the playbooks live in
`~/.almanac/playbooks`. Keep one lineage: playbook logic goes here, never
back into Almanac.

## Rules for work in this repo

- Public repo. Never name other memory or context tools anywhere (code,
  tests, docs, commits). Folders the miner should skip go in the
  `skip_folders` setting, not the code.
- Commits: author `Veertej Sehdave <vssehdav@uwaterloo.ca>`, no
  co-author or "Generated with" lines, Conventional Commits
  (`type(scope): description`, subject at most 50 characters, no period,
  body wrapped at 72 saying why).
- Tests: `PYTHONPATH=src python -m unittest discover -s tests`. Read the
  real exit code, not a piped tail.
- Test the plugin in a real session with
  `almanac sandbox --plugin <this folder>/src/getplaybook/plugin`, as for
  Logbook and Pocketbook.
- Work in the OneDrive GitHub folder; use worktrees for branches.

## Open work, in order

Verified live (sandbox, 2026-11-21): the `UserPromptSubmit` note reaches
the model, and an `enforce` deny from `PreToolUse` reaches it and makes it
call `playbook_run`. The sandbox drops `PLAYBOOK_HOME`, so its hooks use
`~/.playbook`.

1. Not yet seen live: a `PostToolUse` route offer, `auto` in the
   `UserPromptSubmit` hook, and a deny inside a sub-agent.
2. Live A/B: about 5 tasks x 3 runs, with and without playbooks, main thread
   and one sub-agent type; measure tokens, tool calls, time, success, and
   the offer-to-take rate in the logs. Estimates to test: about 7% of
   main-thread cost, more for sub-agents (test-run and poll chains).
