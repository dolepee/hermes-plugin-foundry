import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import foundry_core
import plugin


class FoundryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve() / "foundry"
        self.gen = self.root / "generated"
        self._env = patch.dict(os.environ, {"HERMES_PLUGIN_FOUNDRY_ROOT": str(self.root)})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_parses_folder_and_extension(self):
        spec = foundry_core.parse_request("Watch /tmp/invoices for new PDF files")
        self.assertEqual(spec["folder"], "/tmp/invoices")
        self.assertEqual(spec["extension"], ".pdf")
        self.assertEqual(spec["archetype"], "sqlite_folder_watcher")

    def test_builds_and_tests_a_plugin(self):
        out = self.gen / "invoices"
        result = foundry_core.build_and_test(
            "Watch /tmp/invoices for new PDF files", output_dir=str(out)
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue((out / "plugin.yaml").exists())
        self.assertTrue((out / "tests" / "test_plugin.py").exists())

    def test_default_output_is_under_the_foundry_root(self):
        result = foundry_core.build("Watch /tmp/invoices for new PDF files")
        target = Path(result["spec"]["path"])
        self.assertEqual(target.parent, self.gen)
        self.assertEqual(target.name, "invoices-digest")
        self.assertTrue(target.exists())
        self.assertIn("invoices-digest", [row["name"] for row in foundry_core.list_generated()])

    def test_bare_output_name_lands_under_the_root(self):
        result = foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir="invoices")
        self.assertEqual(Path(result["spec"]["path"]), self.gen / "invoices")

    def test_refuses_to_overwrite(self):
        out = self.gen / "invoices"
        foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))
        with self.assertRaises(FileExistsError):
            foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))

    def test_rejects_output_dir_outside_root(self):
        for outside in (Path(self._tmp.name) / "elsewhere", Path("/tmp/foundry-outside-test")):
            with self.assertRaises(foundry_core.OutsideFoundryRoot):
                foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(outside))

    def test_rejects_parent_escape(self):
        with self.assertRaises(foundry_core.OutsideFoundryRoot):
            foundry_core.build("Watch /tmp/invoices for new PDF files",
                               output_dir=str(self.gen / ".." / "escaped"))

    def test_rejects_symlink_escape(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        self.gen.mkdir(parents=True)
        (self.gen / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(foundry_core.OutsideFoundryRoot):
            foundry_core.run_tests(self.gen / "link")

    def test_builds_api_poller(self):
        out = self.gen / "poller"
        result = foundry_core.build_and_test(
            "Poll https://api.github.com/repos/NousResearch/hermes-agent/releases?per_page=5 every 15 minutes",
            output_dir=str(out),
        )
        self.assertTrue(result["ok"], result)
        self.assertIn("seen_items", (out / "plugin.py").read_text())

    def test_static_scan_flags_unsafe_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plugin.py"
            path.write_text("import os\nos.system('echo bad')\n")
            result = foundry_core.scan_code(tmp)
            self.assertFalse(result["ok"])
            self.assertTrue(result["findings"])

    def test_run_tests_refuses_directory_outside_root(self):
        with self.assertRaises(foundry_core.OutsideFoundryRoot):
            foundry_core.run_tests(self._tmp.name)

    def test_test_subprocess_gets_a_minimal_environment(self):
        out = self.gen / "envcheck"
        foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))
        probe = (
            "import os, unittest\n\n"
            "class EnvTests(unittest.TestCase):\n"
            "    def test_no_secret(self):\n"
            "        self.assertIsNone(os.environ.get('FAKE_PROVIDER_KEY'))\n"
            "    def test_has_path_and_home(self):\n"
            "        self.assertTrue(os.environ.get('PATH'))\n"
            "        self.assertTrue(os.environ.get('HOME'))\n"
        )
        (out / "tests" / "test_env.py").write_text(probe)
        with patch.dict(os.environ, {"FAKE_PROVIDER_KEY": "sk-should-not-leak"}):
            result = foundry_core.run_tests(out)
        self.assertTrue(result["ok"], result)
        self.assertNotIn("sk-should-not-leak", json.dumps(result))

    def test_install_uses_given_hermes_home(self):
        out = self.gen / "installable"
        foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))
        home = Path(self._tmp.name) / "hermes-home"
        target = foundry_core.install_plugin(out, "installable", hermes_home=home)
        self.assertEqual(Path(target), home / "plugins" / "installable")
        self.assertTrue((home / "plugins" / "installable" / "plugin.yaml").exists())

    def test_catalog_reads_only_inside_root(self):
        out = self.gen / "catalog"
        foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))
        self.assertTrue(foundry_core.read_catalog(out)["ok"])
        with self.assertRaises(foundry_core.OutsideFoundryRoot):
            foundry_core.read_catalog(self._tmp.name)


class PluginToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve() / "foundry"
        self.gen = self.root / "generated"
        self._env = patch.dict(os.environ, {"HERMES_PLUGIN_FOUNDRY_ROOT": str(self.root)})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.tools = {schema["name"]: schema for schema in plugin.TOOL_SCHEMAS}

    def test_build_tool_exposes_no_install_flag(self):
        properties = self.tools["foundry_build"]["parameters"]["properties"]
        self.assertNotIn("install", properties)
        self.assertEqual(set(properties), {"request", "output_dir"})

    def test_hook_ignores_paths_inside_root(self):
        self.assertIsNone(plugin._on_pre_tool_call(
            tool_name="foundry_test", args={"path": str(self.gen / "demo")}))

    def test_hook_ignores_other_tools(self):
        self.assertIsNone(plugin._on_pre_tool_call(
            tool_name="foundry_plan", args={"request": "Watch /tmp for PDFs"}))

    def test_hook_requests_approval_outside_root(self):
        result = plugin._on_pre_tool_call(
            tool_name="foundry_test", args={"path": str(self._tmp.name)})
        self.assertEqual(result["action"], "approve")
        self.assertIn("outside", result["message"])
        self.assertTrue(result["rule_key"].startswith("plugin_foundry:foundry_test:"))

    def test_handler_refuses_outside_path_without_approval(self):
        called = []
        with patch.object(plugin.core, "run_tests", side_effect=lambda *a, **k: called.append(k)):
            result = json.loads(plugin._handler("foundry_test")({"path": str(self._tmp.name)}))
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_approval"])
        self.assertEqual(called, [])

    def test_handler_runs_outside_path_after_approval(self):
        path = str(self._tmp.name)
        plugin._on_pre_tool_call(tool_name="foundry_test", args={"path": path})
        seen = {}

        def fake_run(target, **kwargs):
            seen.update(target=target, kwargs=kwargs)
            return {"ok": True}

        with patch.object(plugin.core, "run_tests", side_effect=fake_run):
            result = json.loads(plugin._handler("foundry_test")({"path": path}))
        self.assertTrue(result["ok"])
        self.assertTrue(seen["kwargs"]["allow_outside_root"])

    def test_handler_never_raises(self):
        result = json.loads(plugin._handler("foundry_build")({"request": ""}))
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_plan_handler_returns_json_text(self):
        raw = plugin._handler("foundry_plan")({"request": "Watch /tmp for PDFs"})
        self.assertIsInstance(raw, str)
        self.assertEqual(json.loads(raw)["extension"], ".pdf")
        path = str(self._tmp.name)
        plugin._on_pre_tool_call(tool_name="foundry_test", args={"path": path})
        self.assertTrue(plugin._consume_approval("foundry_test", path))
        self.assertFalse(plugin._consume_approval("foundry_test", path))

    def test_handler_builds_inside_root_without_approval(self):
        result = json.loads(plugin._handler("foundry_build")({
            "request": "Watch /tmp/invoices for new PDF files", "output_dir": "inside"}))
        self.assertTrue(result["ok"], result)
        self.assertEqual(Path(result["build"]["spec"]["path"]), self.gen / "inside")

    def test_register_declares_the_hook(self):
        tools, hooks = [], []

        class Ctx:
            def register_tool(self, **kwargs):
                tools.append(kwargs["name"])

            def register_hook(self, name, callback):
                hooks.append((name, callback))

        plugin.register(Ctx())
        self.assertEqual(sorted(tools), sorted(self.tools))
        self.assertEqual(hooks[0][0], "pre_tool_call")
        self.assertTrue(callable(hooks[0][1]))


if __name__ == "__main__":
    unittest.main()
