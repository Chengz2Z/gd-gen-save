from __future__ import annotations

from pathlib import Path
from unittest import mock
import sys
import unittest


TOOL_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = TOOL_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from runtime_paths import (
    data_directory,
    database_directory,
    gui_config_path,
    program_directory,
)


class RuntimePathTests(unittest.TestCase):
    def test_devotion_skills_database_uses_current_filename(self):
        database = TOOL_DIR / "resources" / "database"
        self.assertTrue((database / "devotion_skills.json").is_file())
        self.assertFalse((database / "all_devotion_skills.json").exists())

    def test_source_config_uses_isolated_runtime_directory(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertEqual(program_directory(), TOOL_DIR / "artifacts" / "runtime")
            self.assertEqual(
                gui_config_path(),
                TOOL_DIR / "artifacts" / "runtime" / "config" / "config.json",
            )
            self.assertEqual(data_directory(), TOOL_DIR / "resources")
            self.assertEqual(database_directory(), TOOL_DIR / "resources" / "database")

    def test_packaged_config_is_beside_executable(self):
        executable = TOOL_DIR / "release" / "GenerateSave.exe"
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(sys, "executable", str(executable)),
        ):
            self.assertEqual(program_directory(), executable.parent)
            self.assertEqual(
                gui_config_path(), executable.parent / "config" / "config.json"
            )
            self.assertEqual(data_directory(), executable.parent / "data")
            self.assertEqual(database_directory(), executable.parent / "data")


if __name__ == "__main__":
    unittest.main()
