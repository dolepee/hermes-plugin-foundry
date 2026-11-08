# Plugin Foundry

Canonical source: [dolepee/hermes-plugin-foundry](https://github.com/dolepee/hermes-plugin-foundry).
The repository was transferred with its history and original authorship intact.
See [CONTRIBUTING.md](CONTRIBUTING.md) for tests and the exact-head Codex review
requirement, and [SECURITY.md](SECURITY.md) for execution limits.

Describe a narrow plugin. Foundry emits a native Hermes plugin and runs its
tests. Installing the result is a separate, human step.

This is a spike. It supports two archetypes: a SQLite-backed folder watcher and
a SQLite-backed JSON API poller, each with one tool. Generated code must be
reviewed before installing or publishing.

## Where it writes

Everything Foundry generates lives under one root:

```
<hermes home>/plugin-data/plugin_foundry/generated/
```

Set `HERMES_PLUGIN_FOUNDRY_ROOT` to use a different root. `foundry_build`
refuses any `output_dir` that resolves outside it (symlinks are followed), and
`foundry_test` / `foundry_catalog` refuse directories outside it. A path outside
the root raises a `pre_tool_call` approval, and only an approved call proceeds.

Generated tests run in a subprocess with a minimal environment — `PATH`, `HOME`,
locale and temp-dir variables only. Provider keys and the rest of the Hermes
environment are not passed through.

## CLI

For local runs and code review:

```bash
python3 foundry_cli.py plan "Watch ~/Invoices for new PDF files"
python3 foundry_cli.py build "Watch ~/Invoices for new PDF files"
python3 foundry_cli.py build "Watch ~/Invoices for new PDF files" --install
python3 foundry_cli.py test <generated dir>
python3 foundry_cli.py list
python3 foundry_cli.py root
```

`--install` copies the generated plugin into `<hermes home>/plugins/<slug>/`.
The tools never install: that stays a deliberate, human-run step.

## Tools

- `foundry_plan` — parse a request into a spec.
- `foundry_build` — generate the plugin under the Foundry root, run its tests.
- `foundry_test` — run a generated plugin's tests.
- `foundry_list` — list generated plugins.
- `foundry_catalog` — read the generated catalog draft.

## Boundary

No auto-merge and no automatic catalog PR. No install from the model-facing
tools. Static scan and generated tests run before a human reviews and installs
the code. The request cannot supply shell commands, and Foundry itself does not
reach the network.
