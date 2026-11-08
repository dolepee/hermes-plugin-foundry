# Plugin Foundry contribution requirements

The canonical repository is `dolepee/hermes-plugin-foundry`. Contributors,
including Saint, work through branches and pull requests, never direct pushes
to `main`. Preserve the transferred history; do not create another source repo.

Before every merge, post a top-level `@codex review` with the full current head
SHA. Require a completed clean review of that exact head, green required CI and
resolution of all actionable Codex threads. Any new commit requires a new
review. Queued reviews, acknowledgements and elapsed time are not approval.
Use `gh pr merge --match-head-commit <full-sha>` after the gate passes.

Run `python -m unittest discover -s tests -v` and the official Hermes admission
validator for packaging changes. Changes to tool dispatch, approval or process
execution also require real Hermes execution evidence, including denied calls.
Calling a handler directly does not prove that the host approval gate ran.

Keep generated outputs, test runtime state, recordings, narration, submission
notes and competition strategy outside this repository. Do not publish provider
credentials or private trace contents. Product reproduction docs belong here.

Do not describe path checks, environment filtering or approval prompts as an OS
sandbox. Do not weaken a failing check to obtain a passing demonstration.
