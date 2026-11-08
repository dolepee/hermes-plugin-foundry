"""Pinned-Hermes approval and dispatch regression for Plugin Foundry.

This test is deliberately opt-in. It loads the real Hermes PluginManager from a
pinned checkout, lets the host resolve the pre-tool approval and run the tool
dispatcher, and never passes a call id to a plugin hook. Set
FOUNDRY_HERMES_SOURCE to a Hermes checkout with its runtime dependencies to run
it; ordinary CI skips it rather than replacing the host with a direct callback.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = os.environ.get("FOUNDRY_HERMES_SOURCE") or os.environ.get("HERMES_AGENT_SOURCE", "")


@unittest.skipUnless(
    SOURCE
    and (Path(SOURCE) / "hermes_cli" / "plugins.py").is_file()
    and importlib.util.find_spec("ruamel.yaml") is not None,
    "set FOUNDRY_HERMES_SOURCE to a Hermes checkout with runtime dependencies",
)
class HostDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name) / "hermes-home"
        (home / "plugins").mkdir(parents=True)
        shutil.copytree(
            Path(__file__).resolve().parent.parent,
            home / "plugins" / "plugin_foundry",
            ignore=shutil.ignore_patterns(
                ".git", ".cache", ".local", "__pycache__", "out", "tests",
                "demo*", "generated", "*.pyc",
            ),
        )
        (home / "config.yaml").write_text(
            "plugins:\n  enabled:\n    - plugin_foundry\n", encoding="utf-8"
        )
        self.env = patch.dict(
            os.environ,
            {"HERMES_HOME": str(home), "HERMES_INTERACTIVE": "1"},
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("HERMES_PLUGIN_FOUNDRY_ROOT", None)

        sys.path.insert(0, SOURCE)
        from hermes_cli import plugins as lifecycle
        from hermes_cli.plugins import PluginManager
        from tools import approval_context

        self.lifecycle = lifecycle
        self.approval_context = approval_context
        self.manager = PluginManager(scope_key=str(home))
        self.manager.discover_and_load()
        lifecycle._plugin_manager = self.manager
        lifecycle._plugin_managers_by_home[Path(self.manager.scope_key)] = self.manager
        loaded = self.manager._plugins["plugin_foundry"]
        self.assertFalse(loaded.error, loaded.error)
        self.assertIn("pre_tool_call", loaded.hooks_registered)
        self.assertIn("post_approval_response", loaded.hooks_registered)
        self.plugin = sys.modules["hermes_plugins.plugin_foundry.plugin"]

    def prompt(self, *, choice="once", before_answer=None):
        seen = self.prompt_calls = []

        def fake_prompt(*_args, **_kwargs):
            seen.append(self.approval_context._approval_tool_call_id.get())
            if before_answer is not None:
                before_answer()
            return choice

        return patch("tools.approval.prompt_dangerous_approval", side_effect=fake_prompt)

    def call(self, tool, args, call_id, *, choice="once", before_answer=None):
        with self.prompt(choice=choice, before_answer=before_answer):
            raw = __import__("model_tools").handle_function_call(
                tool,
                args,
                task_id="host-session",
                session_id="host-session",
                tool_call_id=call_id,
                user_task="run the Foundry tool",
                skip_tool_execution_middleware=True,
            )
        return json.loads(raw) if isinstance(raw, str) else raw

    def outside_test_dir(self, name):
        root = Path(self.tmp.name) / "outside" / name
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_marker.py").write_text(
            "import pathlib, unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_marker(self):\n"
            f"        pathlib.Path({str(root / 'ran.txt')!r}).write_text('ran')\n",
            encoding="utf-8",
        )
        return root

    def test_two_overlapping_identical_once_grants_are_both_consumable(self):
        """The host, not this test, supplies each call id to the plugin hook."""
        path = self.outside_test_dir("same")
        results = {}

        def invoke(call_id):
            results[call_id] = self.call(
                "foundry_test", {"path": str(path)}, call_id
            )

        threads = [
            threading.Thread(target=invoke, args=(call_id,))
            for call_id in ("call-A", "call-B")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(sorted(self.prompt_calls), ["call-A", "call-B"])
        self.assertTrue(results["call-A"].get("ok"), results["call-A"])
        self.assertTrue(results["call-B"].get("ok"), results["call-B"])
        self.assertTrue((path / "ran.txt").exists())
        self.assertEqual(self.plugin._once_grants, {})

    def test_sourceless_bytecode_change_while_the_host_prompt_is_open_is_not_authorised(self):
        path = self.outside_test_dir("bytecode")
        helper = path / "helper.pyc"
        helper.write_bytes(b"approved-bytecode")

        def replace_bytecode():
            helper.write_bytes(b"replacement-bytecode")

        result = self.call(
            "foundry_test",
            {"path": str(path)},
            "call-PYC",
            before_answer=replace_bytecode,
        )
        self.assertFalse(result.get("ok"), result)
        self.assertTrue(result.get("needs_approval"), result)
        self.assertEqual(self.plugin._once_grants, {})

    def test_source_change_while_the_host_prompt_is_open_is_not_authorised(self):
        path = Path(self.tmp.name) / "outside" / "catalog"
        path.mkdir(parents=True)
        (path / "CATALOG.md").write_text("# approved\n", encoding="utf-8")

        def replace_source():
            (path / "CATALOG.md").write_text("# replacement\n", encoding="utf-8")

        result = self.call(
            "foundry_catalog",
            {"path": str(path)},
            "call-F",
            before_answer=replace_source,
        )
        self.assertFalse(result.get("ok"), result)
        self.assertTrue(result.get("needs_approval"), result)
        self.assertEqual(self.plugin._once_grants, {})


if __name__ == "__main__":
    unittest.main()
