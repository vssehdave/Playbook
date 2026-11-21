---
name: playbook
description: Turn a task the person repeats into a playbook - a decision list plus, where possible, a script that does it with no model in the loop. Use at the end of any multi-step task that will come up again, when a playbook offer says the approach changed, or when the person says a job is routine.
---

# Writing a playbook

A playbook saves the next run from being worked out again. Write one when the
task had several steps and will recur. Do not write one for a one-off or for
work that is mostly judgment.

1. **Trace.** List what you actually did this time, in order: each tool call,
   why it was the right one, where each input came from, and what you checked.
   Add the branches you did not hit but know exist (missing input, a failure, a
   refusal).
2. **Rules.** Turn the trace into a short ordered decision list, one line per
   step: "If X: do Y with Z". No narration. Include the branches not yet
   triggered. Note the skills and agents that were used.
3. **Code.** If every step is mechanical, write `run.py`: standard-library
   Python, arguments in, one JSON object out on stdout, a non-zero exit on
   failure, no model call anywhere, no network unless the task itself is a
   network call. It must have `--help` and a `--self-test` that checks its own
   logic on built-in sample data without touching real files. Run the
   self-test before saving.
   If one step truly needs reading unstructured text, keep everything else in
   the script and leave that single step in the decision list for the model.
4. **Save** with the `playbook_save` tool: a lowercase name, one sentence for
   `when`, two to five `triggers` (short phrases the person would actually
   type, and short phrases in your own wording as you start this work), the
   steps, skills, agents, and the script text if there is one.
   A new or changed script is a draft until the person approves it with
   `playbook approve <name>`; say so in your reply.

When a playbook is offered, follow it. If it was wrong or incomplete, fix the
playbook with `playbook_save` once the task is done.

When saved playbooks pile up that do the same job, call `playbook_dedupe` with
`dry_run` true to see what it would merge, then without it to merge. Approved
playbooks are left alone unless `include_approved` is true.
