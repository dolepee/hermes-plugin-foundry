# Plugin Foundry — 60-second demo script

**0–8s**
Screen: Hermes chat.

> “Hermes has over a thousand plugins. The hard part is not having an idea. It’s turning that idea into a plugin that actually loads and works.”

**8–18s**
Type:

> Poll `https://api.github.com/repos/NousResearch/hermes-agent/releases?per_page=5` every 15 minutes and alert me on new releases.

**18–30s**
Cut to the generated directory:

- `plugin.yaml`
- `__init__.py`
- `plugin.py`
- `SKILL.md`
- `tests/test_plugin.py`
- `CATALOG.md`

> “Foundry wrote a native Hermes plugin. Manifest, entry point, tool schema, SQLite state, tests and a catalog draft.”

**30–42s**
Run the tests:

> 3 tests, 3 passed. Static scan: clean.

> “The generated plugin is not trusted until it proves it loads and behaves.”

**42–54s**
Install and load:

> Plugin installed. Tool registered: `github_poller_poll`.

First poll: five Hermes releases.
Second poll: zero new.

> “It remembers what it has seen. No duplicate alerts.”

**54–60s**
Close:

> “Plugin Foundry. Describe a plugin. Hermes builds it, tests it, and installs it.”

On-screen text: **Plugin Foundry — the plugin that makes plugins.**

## Disclosure

- Real Hermes 0.19.0 load and real GitHub API calls.
- Two archetypes: SQLite folder watcher and SQLite API poller.
- Generated code is reviewed before install or catalog submission.
- No auto-merge and no automatic catalog PR.
