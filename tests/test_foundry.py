import tempfile
import unittest
from pathlib import Path

import foundry_core


class FoundryTests(unittest.TestCase):
    def test_parses_folder_and_extension(self):
        spec = foundry_core.parse_request("Watch /tmp/invoices for new PDF files")
        self.assertEqual(spec["folder"], "/tmp/invoices")
        self.assertEqual(spec["extension"], ".pdf")
        self.assertEqual(spec["archetype"], "sqlite_folder_watcher")

    def test_builds_and_tests_a_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "generated"
            result = foundry_core.build_and_test(
                "Watch /tmp/invoices for new PDF files", output_dir=str(out)
            )
            self.assertTrue(result["ok"])
            self.assertTrue((out / "plugin.yaml").exists())
            self.assertTrue((out / "tests" / "test_plugin.py").exists())

    def test_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "generated"
            foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))
            with self.assertRaises(FileExistsError):
                foundry_core.build("Watch /tmp/invoices for new PDF files", output_dir=str(out))

    def test_builds_api_poller(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "generated"
            result = foundry_core.build_and_test(
                "Poll https://api.github.com/repos/NousResearch/hermes-agent/releases?per_page=5 every 15 minutes",
                output_dir=str(out),
            )
            self.assertTrue(result["ok"])
            self.assertIn("seen_items", (out / "plugin.py").read_text())

    def test_static_scan_flags_unsafe_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plugin.py"
            path.write_text("import os\nos.system('echo bad')\n")
            result = foundry_core.scan_code(tmp)
            self.assertFalse(result["ok"])
            self.assertTrue(result["findings"])


if __name__ == "__main__":
    unittest.main()
