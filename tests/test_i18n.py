from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


TOOL_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = TOOL_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from i18n import LanguageManager


class LanguagePackTests(unittest.TestCase):
    def test_all_packs_have_the_default_keys(self):
        manager = LanguageManager()
        default = manager.packs["zh_CN"]
        for code, pack in manager.packs.items():
            with self.subTest(language=code):
                self.assertEqual(set(default["strings"]), set(pack["strings"]))
                self.assertEqual(
                    set(default["crafting_bonuses"]),
                    set(pack["crafting_bonuses"]),
                )
                self.assertEqual(set(default["messages"]), set(pack["messages"]))

    def test_configured_language_and_formatting(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "gui_config.json"
            config.write_text(json.dumps({"language": "en_US"}), encoding="utf-8")
            manager = LanguageManager(config)
            self.assertEqual(manager.language, "en_US")
            self.assertEqual(
                manager.text("log.level", level=100),
                "      Level: 100",
            )
            self.assertEqual(
                manager.message("角色名称不能为空"),
                "Character name cannot be empty.",
            )

    def test_missing_translation_falls_back_to_chinese(self):
        manager = LanguageManager()
        manager.language = "en_US"
        manager.packs["en_US"]["strings"].pop("action.generate")
        self.assertEqual(manager.text("action.generate"), "生成角色存档")

    def test_incomplete_external_chinese_pack_keeps_bundled_fallback(self):
        manager = LanguageManager()
        manager.packs["zh_CN"] = {"strings": {}, "crafting_bonuses": {}}
        manager.language = "zh_CN"
        self.assertEqual(manager.text("action.generate"), "生成角色存档")


if __name__ == "__main__":
    unittest.main()
