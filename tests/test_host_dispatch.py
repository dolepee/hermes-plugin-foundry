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
import subprocess
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
        """An outside tree plus an output directory outside its approved bytes.

        The approval identity covers every byte under the approved tree, so a
        successful run must not write its marker (or anything else) back into
        that tree: that would invalidate a concurrently captured identity by
        design.
        """
        root = Path(self.tmp.name) / "outside" / name
        out = Path(self.tmp.name) / "out" / name
        (root / "tests").mkdir(parents=True)
        out.mkdir(parents=True, exist_ok=True)
        (root / "tests" / "test_marker.py").write_text(
            "import pathlib, unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_marker(self):\n"
            f"        pathlib.Path({str(out / 'ran.txt')!r}).write_text('ran')\n",
            encoding="utf-8",
        )
        return root, out

    def test_two_overlapping_identical_once_grants_are_both_consumable(self):
        """The host, not this test, supplies each call id to the plugin hook."""
        path, out = self.outside_test_dir("same")
        approved_identity = self.plugin.core.approval_identity("foundry_test", str(path))
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
        self.assertTrue((out / "ran.txt").exists())
        self.assertEqual(self.plugin._once_grants, {})
        # Neither run may mutate the approved input bytes.
        self.assertEqual(
            self.plugin.core.approval_identity("foundry_test", str(path)),
            approved_identity,
        )

    def test_changed_source_after_a_successful_session_run_requests_new_approval(self):
        """A session grant covers the approved bytes, not the path forever."""
        path, out = self.outside_test_dir("mutation")
        first = self.call(
            "foundry_test", {"path": str(path)}, "call-S1", choice="session"
        )
        self.assertTrue(first.get("ok"), first)
        self.assertEqual(self.prompt_calls, ["call-S1"])

        # Unchanged bytes reuse the session grant without another prompt.
        second = self.call("foundry_test", {"path": str(path)}, "call-S2")
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(self.prompt_calls, [])

        # Changing the test source must ask again; the new bytes run only
        # after the new decision.
        test_file = path / "tests" / "test_marker.py"
        test_file.write_text(
            test_file.read_text(encoding="utf-8")
            + "        pathlib.Path("
            + repr(str(out / "changed.txt"))
            + ").write_text('changed')\n",
            encoding="utf-8",
        )
        third = self.call("foundry_test", {"path": str(path)}, "call-S3")
        self.assertEqual(self.prompt_calls, ["call-S3"])
        self.assertTrue(third.get("ok"), third)
        self.assertTrue((out / "changed.txt").exists())

    def test_directory_alias_swap_with_identical_bytes_is_not_authorised(self):
        path, _out = self.outside_test_dir("alias")
        first = Path(self.tmp.name) / "alias-first"
        second = Path(self.tmp.name) / "alias-second"
        for folder in (first, second):
            (folder / "tests").mkdir(parents=True)
            (folder / "tests" / "test_marker.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n"
                "    def test_ok(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
        alias = path / "alias"
        alias.symlink_to(first, target_is_directory=True)

        def replace_alias():
            alias.unlink()
            alias.symlink_to(second, target_is_directory=True)

        result = self.call(
            "foundry_test",
            {"path": str(path)},
            "call-ALIAS",
            before_answer=replace_alias,
        )
        self.assertFalse(result.get("ok"), result)
        self.assertTrue(result.get("needs_approval"), result)
        self.assertEqual(self.plugin._once_grants, {})

    def test_sourceless_bytecode_change_while_the_host_prompt_is_open_is_not_authorised(self):
        path, _out = self.outside_test_dir("bytecode")
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

    def test_executed_data_file_change_while_the_host_prompt_is_open_is_not_authorised(self):
        path, _out = self.outside_test_dir("payload")
        payload = path / "tests" / "payload.txt"
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_text("approved-payload", encoding="utf-8")

        def replace_payload():
            payload.write_text("replacement-payload", encoding="utf-8")

        result = self.call(
            "foundry_test",
            {"path": str(path)},
            "call-PAYLOAD",
            before_answer=replace_payload,
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
