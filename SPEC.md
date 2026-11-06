# Plugin Foundry — one-page spec

## The problem

Hermes has 1,063 marketplace plugins. The bottleneck is no longer ideas; it is turning a narrow idea into a native plugin that actually loads, registers tools, handles failure and ships with tests.

## The product

Describe a plugin in one sentence. Foundry emits a native Hermes plugin, runs its tests, and optionally installs it.

**Tagline:** “Describe a plugin. Hermes builds it, tests it, and installs it.”

## MVP archetypes

Two narrow ones:

1. A SQLite-backed folder watcher with one tool.
2. A SQLite-backed JSON API poller with one tool.

Input:

> Watch `/path` for new PDF files and show me only what changed.

or:

> Poll `https://api.github.com/repos/NousResearch/hermes-agent/releases` every 15 minutes.

Output:

- `plugin.yaml`
- `__init__.py`
- `plugin.py`
- `SKILL.md`
- `README.md`
- `tests/test_plugin.py`
- `CATALOG.md`
- `CATALOG.yaml` (entry draft with repo/SHA placeholders)

The folder watcher stores a fingerprint per file and returns only new or changed files. The API poller stores seen item keys and returns only new items. A missing folder or failed request is an error, not an empty success. Neither uploads file contents.

## What is real now

- Foundry loads as a Hermes plugin on 0.19.0 and passes the current v0.23.0 validator and doctor with no warnings.
- `foundry_build` generated `github-poller`.
- 3/3 generated tests passed.
- Static scan passed with no findings.
- It installed into the Hermes plugin directory.
- It loaded as a second real plugin.
- First live poll found 5 Hermes releases; second poll returned 0 new.
- Catalog draft generated.
- Current `hermes plugins validate` passes for both Foundry and the generated plugin.
- Current `hermes plugins doctor` passes for both.
- Loaded in a fresh v0.23.0 runtime with `requires_hermes: ">=0.21.5"`; `foundry_plan` returned the expected spec.
- Evidence: `out/foundry-real-results-v3.json`.

## Demo, 60 seconds

1. One sentence to Foundry.
2. Show the generated files and manifest.
3. Run the generated tests: 3/3 green.
4. Install and load the new plugin.
5. Poll the real GitHub releases endpoint: 5 new releases; poll again: zero new.
6. Show the catalog draft.
7. Close on the point: “This is the plugin that makes plugins.”

## Five-day plan

- Day 1: harden packaging and error handling; current validator already passes.
- Day 2: harden the API poller archetype and add a hook.
- Day 3: add secret scan, dependency check and a human review gate (static scan already runs).
- Day 4: generate README, disclosure block and catalog PR draft; record demo.
- Day 5: polish, submit catalog PR, post the under-90-second demo.

## Boundaries

- No arbitrary code generation in the MVP.
- No auto-install without review.
- No auto-merge or automatic catalog submission.
- Generated code is a starting point, not a security audit.

## Why it fits the judges

- **Usefulness:** every Hermes builder needs it; every plugin gets better.
- **Reliability:** generation is gated by tests, not vibes.
- **Polish:** manifest, entry point, tool, skill, README and tests in one pass.
- **Creativity:** the agent builds tools, the pattern past winners rewarded.
- **Platform value:** it improves the marketplace, not one user’s workflow.
