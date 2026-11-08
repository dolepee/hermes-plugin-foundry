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

    def test_generated_watcher_tests_follow_the_selected_extension(self):
        out = self.gen / "spreadsheets"
        result = foundry_core.build_and_test(
            "Watch /tmp/invoices for new .csv files", output_dir=str(out)
        )
        self.assertTrue(result["ok"], result)
        self.assertIn('plugin.EXTENSION', (out / "tests" / "test_plugin.py").read_text())

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


    def test_run_tests_keeps_interpreter_cache_outside_the_approved_tree(self):
        path = Path(self._tmp.name) / "outside-cache"
        (path / "tests").mkdir(parents=True)
        (path / "tests" / "test_ok.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        before = foundry_core.approval_identity("foundry_test", str(path))
        result = foundry_core.run_tests(path, allow_outside_root=True)
        self.assertTrue(result["ok"], result)
        after = foundry_core.approval_identity("foundry_test", str(path))
        self.assertEqual(before, after)
        self.assertFalse(any(path.rglob("__pycache__")))

    def test_approval_identity_records_each_directory_alias(self):
        path = Path(self._tmp.name) / "alias-tree"
        shared = Path(self._tmp.name) / "shared-tree"
        (path / "tests").mkdir(parents=True)
        (shared / "tests").mkdir(parents=True)
        (shared / "tests" / "test_marker.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (path / "first").symlink_to(shared, target_is_directory=True)
        (path / "second").symlink_to(shared, target_is_directory=True)
        identity = foundry_core.approval_identity("foundry_test", str(path))
        dirs = {row["path"]: row["resolved"] for row in identity["directories"]}
        self.assertEqual(dirs["first"], str(shared.resolve()))
        self.assertEqual(dirs["second"], str(shared.resolve()))
        self.assertIn(".", dirs)

class ConfinementTests(FoundryTests):
    def test_test_sources_symlinked_outside_need_approval(self):
        out = self.gen / "symlink-tests"
        foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))
        outside = Path(self._tmp.name) / "external-tests"
        outside.mkdir()
        (outside / "test_external.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n"
        )
        (out / "tests").rename(out / "tests-real")
        (out / "tests").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(foundry_core.OutsideFoundryRoot):
            foundry_core.run_tests(out)
        request = plugin._on_pre_tool_call(
            tool_name="foundry_test", args={"path": str(out)}, session_id="symlink-session")
        self.assertEqual(request["action"], "approve")
        blocked = json.loads(plugin._handler("foundry_test")(
            {"path": str(out)}, session_id="symlink-session"))
        self.assertFalse(blocked["ok"])
        self.assertTrue(blocked["needs_approval"])
        plugin._on_post_approval_response(
            pattern_key="plugin_rule:" + request["rule_key"], choice="once",
            session_id="symlink-session")
        allowed = json.loads(plugin._handler("foundry_test")(
            {"path": str(out)}, session_id="symlink-session"))
        self.assertTrue(allowed["ok"], allowed)

    def test_catalog_symlink_outside_needs_approval(self):
        out = self.gen / "symlink-catalog"
        foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))
        outside = Path(self._tmp.name) / "secret.md"
        outside.write_text("# private outside file\n")
        (out / "CATALOG.md").unlink()
        (out / "CATALOG.md").symlink_to(outside)
        with self.assertRaises(foundry_core.OutsideFoundryRoot):
            foundry_core.read_catalog(out)
        request = plugin._on_pre_tool_call(
            tool_name="foundry_catalog", args={"path": str(out)}, session_id="catalog-session")
        self.assertEqual(request["action"], "approve")
        blocked = json.loads(plugin._handler("foundry_catalog")(
            {"path": str(out)}, session_id="catalog-session"))
        self.assertFalse(blocked["ok"])
        self.assertTrue(blocked["needs_approval"])
        plugin._on_post_approval_response(
            pattern_key="plugin_rule:" + request["rule_key"], choice="once",
            session_id="catalog-session")
        allowed = json.loads(plugin._handler("foundry_catalog")(
            {"path": str(out)}, session_id="catalog-session"))
        self.assertTrue(allowed["ok"], allowed)
        self.assertIn("private outside file", allowed["content"])

    def test_test_tree_approval_is_invalidated_by_a_swapped_symlink(self):
        out = self.gen / "swap-tests"
        foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))
        first = Path(self._tmp.name) / "first-tests"
        second = Path(self._tmp.name) / "second-tests"
        for folder in (first, second):
            folder.mkdir()
            (folder / "test_external.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n")
        (out / "tests").rename(out / "tests-real")
        (out / "tests").symlink_to(first, target_is_directory=True)
        request = plugin._on_pre_tool_call(
            tool_name="foundry_test", args={"path": str(out)}, session_id="swap-tests-session")
        plugin._on_post_approval_response(
            pattern_key="plugin_rule:" + request["rule_key"], choice="once",
            session_id="swap-tests-session")
        (out / "tests").unlink()
        (out / "tests").symlink_to(second, target_is_directory=True)
        result = json.loads(plugin._handler("foundry_test")(
            {"path": str(out)}, session_id="swap-tests-session"))
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_approval"])

    def test_catalog_approval_is_invalidated_by_a_swapped_symlink(self):
        out = self.gen / "swap-catalog"
        foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))
        first = Path(self._tmp.name) / "first.md"
        second = Path(self._tmp.name) / "second.md"
        first.write_text("# first\n")
        second.write_text("# second\n")
        (out / "CATALOG.md").unlink()
        (out / "CATALOG.md").symlink_to(first)
        request = plugin._on_pre_tool_call(
            tool_name="foundry_catalog", args={"path": str(out)}, session_id="swap-session")
        plugin._on_post_approval_response(
            pattern_key="plugin_rule:" + request["rule_key"], choice="once",
            session_id="swap-session")
        (out / "CATALOG.md").unlink()
        (out / "CATALOG.md").symlink_to(second)
        result = json.loads(plugin._handler("foundry_catalog")(
            {"path": str(out)}, session_id="swap-session"))
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_approval"])

    def test_nested_in_root_symlink_descendant_outside_needs_approval(self):
        out = self.gen / "nested-tests"
        foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))
        shared = self.gen / "shared-tests"
        shared.mkdir()
        outside = Path(self._tmp.name) / "outside-helper.py"
        outside.write_text("VALUE = 'outside'\n")
        (shared / "helper.py").symlink_to(outside)
        (out / "tests").rename(out / "tests-real")
        (out / "tests").symlink_to(shared, target_is_directory=True)
        with self.assertRaises(foundry_core.OutsideFoundryRoot):
            foundry_core.run_tests(out)
        request = plugin._on_pre_tool_call(
            tool_name="foundry_test", args={"path": str(out)}, session_id="nested-session")
        self.assertEqual(request["action"], "approve")

    def test_outside_catalog_bytes_change_invalidates_approval(self):
        out = self.gen / "content-catalog"
        foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))
        outside = Path(self._tmp.name) / "outside-catalog.md"
        outside.write_text("# first\n")
        (out / "CATALOG.md").unlink()
        (out / "CATALOG.md").symlink_to(outside)
        request = plugin._on_pre_tool_call(
            tool_name="foundry_catalog", args={"path": str(out)}, session_id="content-session")
        plugin._on_post_approval_response(
            pattern_key="plugin_rule:" + request["rule_key"], choice="once",
            session_id="content-session", tool_call_id="call-content")
        outside.write_text("# second\n")
        result = json.loads(plugin._handler("foundry_catalog")(
            {"path": str(out)}, session_id="content-session", tool_call_id="call-content"))
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_approval"])

    def test_source_change_while_approval_is_pending_is_not_authorised(self):
        out = self.gen / "pending-catalog"
        foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))
        outside = Path(self._tmp.name) / "pending-catalog.md"
        outside.write_text("# approved\n")
        (out / "CATALOG.md").unlink()
        (out / "CATALOG.md").symlink_to(outside)
        for choice in ("once", "session", "always"):
            with self.subTest(choice=choice):
                outside.write_text("# approved\n")
                request = plugin._on_pre_tool_call(
                    tool_name="foundry_catalog", args={"path": str(out)},
                    session_id=f"pending-{choice}", tool_call_id=f"call-{choice}")
                self.assertEqual(request["action"], "approve")
                # The target changes after the prompt went up but before the
                # human answered: the decision names the old bytes only.
                outside.write_text("# replacement\n")
                plugin._on_post_approval_response(
                    pattern_key="plugin_rule:" + request["rule_key"], choice=choice,
                    session_id=f"pending-{choice}", tool_call_id=f"call-{choice}")
                result = json.loads(plugin._handler("foundry_catalog")(
                    {"path": str(out)}, session_id=f"pending-{choice}",
                    tool_call_id=f"call-{choice}"))
                self.assertFalse(result["ok"], result)
                self.assertTrue(result["needs_approval"], result)
                again = plugin._on_pre_tool_call(
                    tool_name="foundry_catalog", args={"path": str(out)},
                    session_id=f"pending-{choice}", tool_call_id=f"call-{choice}-2")
                self.assertEqual(again["action"], "approve")

    def test_sourceless_bytecode_change_while_approval_is_pending_is_not_authorised(self):
        path = Path(self._tmp.name) / "outside-bytecode"
        (path / "tests").mkdir(parents=True)
        (path / "tests" / "test_marker.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n",
            encoding="utf-8",
        )
        helper = path / "helper.pyc"
        for choice in ("once", "session", "always"):
            with self.subTest(choice=choice):
                helper.write_bytes(b"approved-bytecode")
                request = plugin._on_pre_tool_call(
                    tool_name="foundry_test", args={"path": str(path)},
                    session_id=f"bytecode-{choice}", tool_call_id=f"call-{choice}")
                self.assertEqual(request["action"], "approve")
                helper.write_bytes(b"replacement-bytecode")
                plugin._on_post_approval_response(
                    pattern_key="plugin_rule:" + request["rule_key"], choice=choice,
                    session_id=f"bytecode-{choice}", tool_call_id=f"call-{choice}")
                with patch.object(plugin.core, "run_tests", return_value={"ok": True}) as run:
                    result = json.loads(plugin._handler("foundry_test")(
                        {"path": str(path)}, session_id=f"bytecode-{choice}",
                        tool_call_id=f"call-{choice}"))
                self.assertFalse(result["ok"], result)
                self.assertTrue(result["needs_approval"], result)
                run.assert_not_called()

    def test_executed_data_file_change_while_approval_is_pending_is_not_authorised(self):
        path = Path(self._tmp.name) / "outside-payload"
        (path / "tests").mkdir(parents=True)
        (path / "tests" / "test_marker.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n",
            encoding="utf-8",
        )
        payload = path / "tests" / "payload.txt"
        for choice in ("once", "session", "always"):
            with self.subTest(choice=choice):
                payload.write_text("approved-payload", encoding="utf-8")
                request = plugin._on_pre_tool_call(
                    tool_name="foundry_test", args={"path": str(path)},
                    session_id=f"payload-{choice}", tool_call_id=f"call-payload-{choice}")
                self.assertEqual(request["action"], "approve")
                payload.write_text("replacement-payload", encoding="utf-8")
                plugin._on_post_approval_response(
                    pattern_key="plugin_rule:" + request["rule_key"], choice=choice,
                    session_id=f"payload-{choice}", tool_call_id=f"call-payload-{choice}")
                with patch.object(plugin.core, "run_tests", return_value={"ok": True}) as run:
                    result = json.loads(plugin._handler("foundry_test")(
                        {"path": str(path)}, session_id=f"payload-{choice}",
                        tool_call_id=f"call-payload-{choice}"))
                self.assertFalse(result["ok"], result)
                self.assertTrue(result["needs_approval"], result)
                run.assert_not_called()

    def test_call_b_cannot_consume_call_a_once_grant(self):
        path = str(self._tmp.name)
        request_a = plugin._on_pre_tool_call(
            tool_name="foundry_test", args={"path": path}, session_id="call-session",
            tool_call_id="call-A")
        plugin._on_pre_tool_call(
            tool_name="foundry_test", args={"path": path}, session_id="call-session",
            tool_call_id="call-B")
        plugin._on_post_approval_response(
            pattern_key="plugin_rule:" + request_a["rule_key"], choice="once",
            session_id="call-session", tool_call_id="call-A")
        blocked = json.loads(plugin._handler("foundry_test")(
            {"path": path}, session_id="call-session", tool_call_id="call-B"))
        self.assertFalse(blocked["ok"])
        self.assertTrue(blocked["needs_approval"])
        with patch.object(plugin.core, "run_tests", return_value={"ok": True}) as run:
            allowed = json.loads(plugin._handler("foundry_test")(
                {"path": path}, session_id="call-session", tool_call_id="call-A"))
        self.assertTrue(allowed["ok"])
        run.assert_called_once()

    def test_session_aliases_share_one_once_grant(self):
        path = str(self._tmp.name)
        request = plugin._on_pre_tool_call(
            tool_name="foundry_test", args={"path": path}, session_id="primary",
            tool_call_id="alias-call")
        plugin._on_post_approval_response(
            pattern_key="plugin_rule:" + request["rule_key"], choice="once",
            session_id="primary", session_key="secondary", tool_call_id="alias-call")
        with patch.object(plugin.core, "run_tests", return_value={"ok": True}) as run:
            first = json.loads(plugin._handler("foundry_test")(
                {"path": path}, session_id="primary", tool_call_id="alias-call"))
            second = json.loads(plugin._handler("foundry_test")(
                {"path": path}, session_id="secondary", tool_call_id="alias-call"))
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertTrue(second["needs_approval"])
        run.assert_called_once()

    def test_expired_session_grant_requests_renewal(self):
        path = str(self._tmp.name)
        request = plugin._on_pre_tool_call(
            tool_name="foundry_test", args={"path": path}, session_id="expiry-session")
        plugin._on_post_approval_response(
            pattern_key="plugin_rule:" + request["rule_key"], choice="session",
            session_id="expiry-session")
        future = plugin.time.monotonic() + plugin.APPROVAL_TTL_SECONDS + 5
        with patch.object(plugin.time, "monotonic", return_value=future):
            renewed = plugin._on_pre_tool_call(
                tool_name="foundry_test", args={"path": path}, session_id="expiry-session")
        self.assertEqual(renewed["action"], "approve")

    def test_concurrent_sessions_keep_separate_grants(self):
        path = str(self._tmp.name)
        requests = {}
        for session in ("session-a", "session-b"):
            requests[session] = plugin._on_pre_tool_call(
                tool_name="foundry_test", args={"path": path}, session_id=session)
        for session, request in requests.items():
            plugin._on_post_approval_response(
                pattern_key="plugin_rule:" + request["rule_key"], choice="once",
                session_id=session)
        with patch.object(plugin.core, "run_tests", return_value={"ok": True}) as run:
            for session in ("session-a", "session-b"):
                result = json.loads(plugin._handler("foundry_test")(
                    {"path": path}, session_id=session))
                self.assertTrue(result["ok"], result)
        self.assertEqual(run.call_count, 2)

    def test_denied_request_is_removed(self):
        path = str(self._tmp.name)
        request = plugin._on_pre_tool_call(
            tool_name="foundry_test", args={"path": path}, session_id="denied-session")
        plugin._on_post_approval_response(
            pattern_key="plugin_rule:" + request["rule_key"], choice="deny",
            session_id="denied-session")
        result = json.loads(plugin._handler("foundry_test")(
            {"path": path}, session_id="denied-session"))
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_approval"])


class CliTests(unittest.TestCase):
    def test_install_is_refused_when_validation_fails(self):
        import foundry_cli
        result = {
            "build": {"spec": {"slug": "bad", "path": "/tmp/bad"}, "files": []},
            "tests": {"ok": False}, "scan": {"ok": True}, "ok": False,
        }
        with patch.object(foundry_cli, "build_and_test", return_value=result), \
             patch.object(foundry_cli, "install_plugin") as install:
            code = foundry_cli.main(["build", "Watch /tmp for PDFs", "--install"])
        self.assertEqual(code, 1)
        install.assert_not_called()


class PluginToolTests(unittest.TestCase):
    def approve(self, tool, path, choice="once", session_id="test-session"):
        request = plugin._on_pre_tool_call(tool_name=tool, args={"path": path}, session_id=session_id)
        self.assertEqual(request["action"], "approve")
        plugin._on_post_approval_response(
            pattern_key="plugin_rule:" + request["rule_key"],
            choice=choice,
            session_id=session_id,
        )
        return request

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve() / "foundry"
        self.gen = self.root / "generated"
        self._env = patch.dict(os.environ, {"HERMES_PLUGIN_FOUNDRY_ROOT": str(self.root)})
        self._env.start()
        self.addCleanup(self._env.stop)
        with plugin._approvals_lock:
            plugin._requests.clear()
            plugin._once_grants.clear()
            plugin._session_grants.clear()
            plugin._permanent_grants.clear()
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
        self.approve("foundry_test", path)
        seen = {}

        def fake_run(target, **kwargs):
            seen.update(target=target, kwargs=kwargs)
            return {"ok": True}

        with patch.object(plugin.core, "run_tests", side_effect=fake_run):
            result = json.loads(plugin._handler("foundry_test")(
                {"path": path}, session_id="test-session"))
        self.assertTrue(result["ok"])
        self.assertTrue(seen["kwargs"]["allow_outside_root"])

    def test_pending_request_alone_is_not_approval(self):
        path = str(self._tmp.name)
        plugin._on_pre_tool_call(tool_name="foundry_test", args={"path": path}, session_id="test-session")
        with patch.object(plugin.core, "run_tests") as run:
            result = json.loads(plugin._handler("foundry_test")(
                {"path": path}, session_id="test-session"))
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_approval"])
        run.assert_not_called()

    def test_denied_and_abandoned_requests_stay_blocked(self):
        path = str(self._tmp.name)
        for choice in ("deny", "timeout", "cancelled"):
            with self.subTest(choice=choice):
                request = plugin._on_pre_tool_call(
                    tool_name="foundry_test", args={"path": path}, session_id="test-session")
                plugin._on_post_approval_response(
                    pattern_key="plugin_rule:" + request["rule_key"],
                    choice=choice,
                    session_id="test-session",
                )
                with patch.object(plugin.core, "run_tests") as run:
                    result = json.loads(plugin._handler("foundry_test")(
                        {"path": path}, session_id="test-session"))
                self.assertFalse(result["ok"])
                self.assertTrue(result["needs_approval"])
                run.assert_not_called()

    def test_approval_does_not_cover_a_changed_path(self):
        path = str(self._tmp.name)
        self.approve("foundry_test", path)
        other = str(Path(self._tmp.name) / "other")
        with patch.object(plugin.core, "run_tests") as run:
            result = json.loads(plugin._handler("foundry_test")(
                {"path": other}, session_id="test-session"))
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_approval"])
        run.assert_not_called()

    def test_handler_never_raises(self):
        result = json.loads(plugin._handler("foundry_build")({"request": ""}))
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_plan_handler_returns_json_text(self):
        raw = plugin._handler("foundry_plan")({"request": "Watch /tmp for PDFs"})
        self.assertIsInstance(raw, str)
        self.assertEqual(json.loads(raw)["extension"], ".pdf")
        path = str(self._tmp.name)
        self.approve("foundry_test", path)
        self.assertTrue(plugin._consume_approval("foundry_test", path, "test-session"))
        self.assertFalse(plugin._consume_approval("foundry_test", path, "test-session"))

    def test_handler_builds_inside_root_without_approval(self):
        result = json.loads(plugin._handler("foundry_build")({
            "request": "Watch /tmp/invoices for new PDF files", "output_dir": "inside"}))
        self.assertTrue(result["ok"], result)
        self.assertEqual(Path(result["build"]["spec"]["path"]), self.gen / "inside")

    def test_register_declares_the_hook(self):
        tools, hooks, middleware = [], [], []

        class Ctx:
            def register_tool(self, **kwargs):
                tools.append(kwargs["name"])

            def register_hook(self, name, callback):
                hooks.append((name, callback))

            def register_middleware(self, kind, callback):
                middleware.append((kind, callback))

        plugin.register(Ctx())
        self.assertEqual(sorted(tools), sorted(self.tools))
        self.assertEqual(hooks[0][0], "pre_tool_call")
        self.assertEqual(hooks[1][0], "post_approval_response")
        self.assertTrue(callable(hooks[0][1]))
        self.assertTrue(callable(hooks[1][1]))
        self.assertEqual(middleware, [("tool_execution", plugin._tool_execution_middleware)])


    def test_handler_uses_host_context_call_id_when_dispatch_omits_it(self):
        path = str(self._tmp.name)
        requests = []
        for call_id in ("call-A", "call-B"):
            requests.append(plugin._on_pre_tool_call(
                tool_name="foundry_test", args={"path": path},
                session_id="host-context", tool_call_id=call_id))
        for request, call_id in zip(requests, ("call-A", "call-B")):
            plugin._on_post_approval_response(
                pattern_key="plugin_rule:" + request["rule_key"], choice="once",
                session_id="host-context", tool_call_id=call_id)
        with unittest.mock.patch.object(plugin.core, "run_tests", return_value={"ok": True}) as run:
            for call_id in ("call-A", "call-B"):
                token = plugin._TOOL_CALL_ID.set(call_id)
                try:
                    result = json.loads(plugin._handler("foundry_test")(
                        {"path": path}, session_id="host-context"))
                finally:
                    plugin._TOOL_CALL_ID.reset(token)
                self.assertTrue(result["ok"], result)
            self.assertEqual(run.call_count, 2)
        self.assertEqual(plugin._once_grants, {})

    def test_handler_without_a_call_id_stays_fail_closed_when_two_grants_match(self):
        path = str(self._tmp.name)
        requests = []
        for call_id in ("call-A", "call-B"):
            requests.append(plugin._on_pre_tool_call(
                tool_name="foundry_test", args={"path": path},
                session_id="ambiguous", tool_call_id=call_id))
        for request, call_id in zip(requests, ("call-A", "call-B")):
            plugin._on_post_approval_response(
                pattern_key="plugin_rule:" + request["rule_key"], choice="once",
                session_id="ambiguous", tool_call_id=call_id)
        with unittest.mock.patch.object(plugin.core, "run_tests", return_value={"ok": True}) as run:
            result = json.loads(plugin._handler("foundry_test")(
                {"path": path}, session_id="ambiguous"))
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["needs_approval"], result)
        run.assert_not_called()

    def test_directory_alias_swap_with_identical_bytes_is_not_authorised(self):
        path = Path(self._tmp.name) / "alias-approval"
        first = Path(self._tmp.name) / "alias-first"
        second = Path(self._tmp.name) / "alias-second"
        for folder in (first, second):
            (folder / "tests").mkdir(parents=True)
            (folder / "tests" / "test_marker.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n"
                "    def test_ok(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
        path.mkdir()
        (path / "alias").symlink_to(first, target_is_directory=True)
        request = plugin._on_pre_tool_call(
            tool_name="foundry_test", args={"path": str(path)},
            session_id="alias-swap", tool_call_id="alias-swap-call")
        self.assertEqual(request["action"], "approve")
        (path / "alias").unlink()
        (path / "alias").symlink_to(second, target_is_directory=True)
        plugin._on_post_approval_response(
            pattern_key="plugin_rule:" + request["rule_key"], choice="once",
            session_id="alias-swap", tool_call_id="alias-swap-call")
        with unittest.mock.patch.object(plugin.core, "run_tests", return_value={"ok": True}) as run:
            result = json.loads(plugin._handler("foundry_test")(
                {"path": str(path)}, session_id="alias-swap", tool_call_id="alias-swap-call"))
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["needs_approval"], result)
        run.assert_not_called()

if __name__ == "__main__":
    unittest.main()
