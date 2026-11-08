# Contributing

Use the canonical repository at https://github.com/dolepee/hermes-plugin-foundry.
The ownership transfer preserved the original Git history and authorship.

Create a branch from current `main`, make the change, then run:

```sh
python -m unittest discover -s tests -v
python -m compileall -q foundry_core.py foundry_cli.py plugin.py tests
hermes plugins validate .
```

The CI validator uses pinned Hermes 0.23.0. Application-level tool and approval
changes need actual registry/approval-path validation as well as unit tests.
Include the exact tested commit and distinguish real host execution from mocks.

Open a pull request describing the problem, resulting behavior and validation.
Request `@codex review` in a top-level comment naming the full final head SHA.
Address actionable findings, rerun affected checks and request another review
after every new commit. Merge only after the exact-head review completes cleanly,
required CI passes and actionable threads are resolved. Use an atomic head guard.

Repository administrators enforce the Codex completion check as well as GitHub's
required CI; a green test run alone does not authorize merging. A review-only
retrospective PR exposes inherited source for review and must not be merged.

Install generated code only after inspecting it. Keep runtime outputs and private
recording/submission material outside version control; see `SECURITY.md` for the
execution boundary.
