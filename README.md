# Playbook

Playbook turns work you repeat with Claude Code into deterministic steps.

You ask for the same kind of job again and again: run the tests, read what failed, run again; commit, push, open a pull
request, wait for CI, merge. Each time the model works the steps out from scratch and pays for every tool call. A
playbook records the steps once, as a decision list and, when every step is mechanical, a script that does the whole job
in one call with no model in the loop.

Python standard library only. No network. The miner only reads your session files.

## What it does

- **Offers a playbook from what Claude is doing, not from your words.** A prompt like "continue" says nothing, but the
  tool calls do. While Claude works, a hook compares the route so far (test, commit, push ...), what Claude says between
  tool calls ("committing and merging") and whether the repo has something to ship. When two of the three agree with an
  approved playbook, Claude is told, once per playbook per turn: call `playbook_run`.
- **Also matches by words.** A playbook's triggers are checked against what you type, as before.
- **Three modes, chosen when you approve.** `suggest` (the default) offers the playbook. `enforce` also refuses a tool
  call that does one of its steps by hand while it is offered, and tells Claude to call `playbook_run`; once a run of it
  fails in that turn, the steps are Claude's again. `auto` runs an argument-free script before Claude sees a matching
  request and hands Claude the result.
- **Nested layers.** A playbook can include others. `playbook_run` runs the included ones in order, stops at the first
  failure, and prints only what failed. The first line is `RESULT: pass` or `RESULT: fail (...)`; a failed run ends with
  `RESUME: playbook_run {...}`, so after the cause is fixed the run goes on from the failed step instead of starting over.
- **Sub-agent playbooks per agent type.** A test-and-poll chain a `verifier` agent runs every day is a playbook for that
  agent type, offered to it and to no other.
- **Finds them for you.** `playbook mine` reads your past sessions (no model) and lists the requests and ways of working
  you repeat. `playbook compile` has one capped Claude Code session write the best one up as a draft; you approve it.

## Install

```
pip install getplaybook && playbook setup
```

`playbook setup` copies the Claude Code plugin that ships inside the package to `~/.playbook/plugin` and runs
`claude plugin marketplace add` and `claude plugin install playbook@playbook` for you (`--dry-run` prints the commands).
Without Claude Code on the machine it says so and leaves just the library and the command installed.

The plugin adds the `playbook` skill, an MCP server (`playbook mcp`) and three hooks. They call the `playbook` command, so
it must be on your PATH. To install the plugin by hand instead, in Claude Code: `/plugin marketplace add
vssehdave/Playbook`, then `/plugin install playbook@playbook`.

## Commands

```
playbook setup [--dry-run]                    install the Claude Code plugin shipped with the package
playbook list | show NAME | remove NAME | path
playbook approve NAME [--mode suggest|enforce|auto]   approve it and choose how it is offered (--auto is --mode auto)
playbook run NAME [ARGS...] [--from STEP]     run an approved playbook, as the playbook_run tool does
playbook mine [--days 30] [--min 3]           find tasks you repeat in past sessions
playbook compile [--top N] [--dry-run]        write the best repeat up as a draft playbook (one Claude Code session)
playbook dedupe [--include-approved]          merge playbooks that do the same route
playbook mcp                                  the MCP server: playbook_find, playbook_run, playbook_save
playbook hook post-tool-use | pre-tool-use | user-prompt-submit
playbook work                                 mine, then compile within the daily cap (what the background run does)
```

Everything lives in `~/.playbook` (`$PLAYBOOK_HOME` to move it): `playbooks/<name>/playbook.json` and `run.py`,
`auto.json` and `auto.key` (your signed modes), `sessions.json` (what the hooks already offered), `config.json`.

Settings, all optional, in `config.json`:

```json
{"playbooks": {"enabled": true, "inject": true, "route": true, "enforce": true, "auto_run": true, "transcript_dirs": ["~/.claude-work"],
               "skip_folders": ["AppData-Local-Temp", "-Temp-", "playbook-compile"], "compile_per_day": 1}}
```

`route` and `inject` turn the two kinds of offer off; `enforce` and `auto_run` turn those modes off; `transcript_dirs` adds Claude Code config folders to mine besides
`~/.claude` and `$CLAUDE_CONFIG_DIR`; `skip_folders` lists parts of session folder names the miner leaves out.

## Safety

- A playbook that Claude or the miner writes is a **draft**. Nothing is offered or run until you approve it.
- A script runs only through `playbook_run`, and only while its text still matches the SHA-256 you approved. Edit the
  file and it is a draft again. `playbook approve NAME` prints the script first (the first 40 lines) and the hash.
- The mode (`approve --mode enforce|auto`) is signed together with the script's hash with a random key kept in
  `~/.playbook/auto.key`. A model that can write files but not run code cannot sign an entry or change its mode, and an
  edited script falls back to `suggest`.
- Scripts run with `python -I`, no shell, a time limit and capped output.
- Stored text is checked on every load: sizes, types, no `system-reminder` tags, no symlinks, valid names only.
- The miner keeps only the text you typed (secrets removed, cut short) and the names of tools, never a tool result.
- The hooks never fail a session: any error means no offer, and exit code 0.

## Does it pay off? A measured note

In one person's history (a single heavy Claude Code user, so treat it as an example and not a promise), the mechanical
shipping steps (commit, push, pull request, merge) were about 9% of the main thread's cost. Test-run and poll chains were
about 25 to 35% of the cost of the sub-agents that ran them. That is why playbooks are offered to sub-agents by type, and
why a run of commands that never needed judgment is worth a script. Your numbers will differ; `playbook mine` shows yours.

## Using it with Almanac

Almanac, the Claude Code proxy this was split from, depends on this package and places the same offers itself at the end of
the newest message, where they cannot disturb its cached prefix. It sets `ALMANAC_PROXY` in the Claude Code it launches,
and the hooks here stay silent when that is set.

MIT licensed.
