"""Steps 1 to 3 of skills/playbook/SKILL.md, the part the compiling session is given (a test keeps the two equal)."""

WRITING = """# Writing a playbook

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
   the script and leave that single step in the decision list for the model."""
