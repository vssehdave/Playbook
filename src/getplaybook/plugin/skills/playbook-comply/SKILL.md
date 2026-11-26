---
name: playbook-comply
description: Check whether a recorded playbook is followed when it is offered, from `playbook stats` and the offer log, and fix it when it is not (reword the trigger, fix or add one step, merge or retire). Use when the owner asks whether playbooks work, when stats shows LOW TAKE or denies, or when work was corrected right after a playbook ran.
---

# Checking that a playbook is followed

Measure first, then change one step. The offer log keeps names and counts
only, never the request or the output.

1. **Numbers.** Run `playbook stats`. Each playbook gives offers, takes,
   denies, passes and fails. At 5 or more offers it adds `take rate NN%`, and
   `LOW TAKE` under 25%. Copy each line as printed.
2. **Expected order.** For each LOW TAKE line, or any playbook with denies or
   fails, run `playbook show NAME`. Its `steps`, in order, are the expected
   sequence. `when` and `triggers` say what work it is offered for.
3. **Corrections.** `playbook stats` prints no correction count, although its
   help text says it does: `mine.corrections` exists but nothing calls it. Say
   the number is unavailable rather than estimate it.
4. **Compare.** Read the playbook's rows in `log.jsonl` (in the Playbook home's
   `logs` folder) and the failed run output:
   - denies with no take: the steps were refused. Check whether `triggers`
     matched work it does not cover.
   - take with no pass or fail: the result was not recorded. Check the run's
     output before trusting the numbers.
   - fail: the step that broke in the output is the wrong step.
   - correction right after use (only if the owner reports one): that step is
     unclear or missing.
   Pick the one step that is wrong, unclear or missing. Change only that step.
5. **Fix, smallest first.**
   - Offered for the wrong work: reword `when` or `triggers`.
   - Step wrong or unclear: reword it, or add `If <signal>: <action>`.
   - Duplicates: `playbook dedupe --dry-run`, then `playbook dedupe`. For steps
     several playbooks share, `playbook dedupe --suggest-includes` lists them.
   - Never followed and no longer needed: `playbook remove NAME`.
   Any edit changes the script hash, so an approved playbook falls back to
   suggest until the owner re-runs `playbook approve NAME`. Do not approve it
   yourself.
6. **Report** per playbook: the stats line, the expected steps, the cause,
   the change, and whether it was applied or left for the owner.

Leave out: the correction count, and any playbook with fewer than 5 offers,
which stats cannot judge.
