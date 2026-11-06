# Plugin Foundry

Describe a narrow plugin. Foundry emits a native Hermes plugin, runs its tests,
and optionally installs it.

This is a spike. It supports two archetypes: a SQLite-backed folder watcher and
a SQLite-backed JSON API poller, each with one tool. Generated code must be
reviewed before installing or publishing.

## CLI

For local testing and the demo:

```bash
python3 foundry_cli.py plan "Watch ~/Invoices for new PDF files"
python3 foundry_cli.py build "Watch ~/Invoices for new PDF files" --output /tmp/invoices-plugin
python3 foundry_cli.py test /tmp/invoices-plugin
python3 foundry_cli.py poll /tmp/github-poller
```

## Tools

- `foundry_plan` — parse a request into a spec.
- `foundry_build` — generate the plugin and run its tests.
- `foundry_test` — run the generated plugin's tests.
- `foundry_list` — list generated plugins.
- `foundry_catalog` — read the generated catalog draft.

## Boundary

No auto-merge and no automatic catalog PR. Static scan and generated tests run
before install; a human still reviews the code. The tests run in a subprocess
with the current Python interpreter; the request cannot supply shell commands.
