from __future__ import annotations

from pathlib import Path
from unittest import mock
import sys
import unittest


TOOL_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = TOOL_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from runtime_paths import gui_config_path, program_directory


class RuntimePathTests(unittest.TestCase):
    def test_source_config_uses_isolated_runtime_directory(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertEqual(program_directory(), TOOL_DIR / "artifacts" / "runtime")
            self.assertEqual(
                gui_config_path(),
                TOOL_DIR / "artifacts" / "runtime" / "config" / "config.json",
            )

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


if __name__ == "__main__":
    unittest.main()
