# Plugin Foundry

Describe a narrow plugin. Foundry emits a native Hermes plugin, runs its tests,
and optionally installs it.

This is a spike. It supports two archetypes: a SQLite-backed folder watcher and
a SQLite-backed JSON API poller, each with one tool. Generated code must be
reviewed before installing or publishing.

## Tools

- `foundry_plan` — parse a request into a spec.
- `foundry_build` — generate the plugin and run its tests.
- `foundry_test` — run the generated plugin's tests.
- `foundry_list` — list generated plugins.
- `foundry_catalog` — read the generated catalog draft.

## Boundary

No auto-merge and no automatic catalog PR. Static scan and generated tests run
before install; a human still reviews the code.
