from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


TOOL_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = TOOL_DIR / "src"
RESOURCE_DIR = TOOL_DIR / "resources"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from generator import (
    ASCENDED_AFFIX_RECORDS,
    GenerationError,
    _apply_skills,
    _blank_item,
    generate_save,
    validate_character_name,
)
from grimtools import GrimToolsBuild, extract_build_id
from save_format import CharacterSave
class SaveFormatTests(unittest.TestCase):
    def test_template_round_trip_is_byte_identical(self):
        path = RESOURCE_DIR / "_template" / "player.gdc"
        original = path.read_bytes()
        save = CharacterSave.from_bytes(original)
        self.assertEqual(save.to_bytes(), original)

    def test_header_name_can_change_length(self):
        save = CharacterSave.load(RESOURCE_DIR / "_template" / "player.gdc")
        save.header.character_name = "\u6784\u7b51\u6d4b\u8bd5"
        loaded = CharacterSave.from_bytes(save.to_bytes())
        self.assertEqual(loaded.header.character_name, "\u6784\u7b51\u6d4b\u8bd5")


class GrimToolsTests(unittest.TestCase):
    def test_extract_build_id(self):
        self.assertEqual(
            extract_build_id("https://www.grimtools.com/calc/NXl7KPWN"),
            "NXl7KPWN",
        )
        self.assertEqual(extract_build_id("NXl7KPWN"), "NXl7KPWN")

    def test_reject_foreign_host(self):
        with self.assertRaises(ValueError):
            extract_build_id("https://example.com/calc/NXl7KPWN")


class GeneratorTests(unittest.TestCase):
    def _build(self) -> GrimToolsBuild:
        skills = [
            {
                "name": "records/skills/playerclass10/_classtraining_class10.dbr",
                "level": 40,
            },
            {"name": "records/skills/devotion/tier1_04a.dbr", "level": 1},
        ]
        return GrimToolsBuild(
            build_id="TEST1234",
            current={"masteries": {"0": "10", "1": "01"}, "created_for_build": "1.3.0.0", "data": {}},
            expanded={
                "created_for_build": "1.3.0.0",
                "data": {
                    "bio": {
                        "level": 100,
                        "attributePoints": 1,
                        "skillPoints": 2,
                        "devotionPoints": 3,
                        "physique": 226,
                        "cunning": 586,
                        "spirit": 210,
                    },
                    "equipment": {
                        "weapon1": {
                            "item": "records/items/gearweapons/melee2h/test.dbr"
                        }
                    },
                    "skills": skills,
                    "itemSkills": [],
                    "transformSkills": [],
                },
            },
        )

    def test_generate_and_verify(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = generate_save(
                self._build(),
                "UnitTestHero",
                RESOURCE_DIR / "_template",
                Path(temporary),
            )
            save = CharacterSave.load(result.player_file)
            self.assertEqual(save.header.character_name, "UnitTestHero")
            self.assertEqual(save.header.player_class_name, "tagSkillClassName0110")
            self.assertEqual(save.block(2).payload["attribute_points"], 1)
            self.assertEqual(
                save.block(3).payload.tail["weapon_sets"][0]["items"][1]["basename"],
                "",
            )
            expected_count = sum(1 for path in (RESOURCE_DIR / "_template").rglob("*") if path.is_file())
            self.assertEqual(sum(1 for path in result.output_directory.rglob("*") if path.is_file()), expected_count)

    def test_reject_invalid_name(self):
        with self.assertRaises(GenerationError):
            validate_character_name("bad/name")

    def test_ascended_affix_id_is_written_as_record_path(self):
        import random

        item = _blank_item(
            random.Random(1),
            {"item": "records/items/test.dbr", "ascendedAffix": "aa16077"},
        )
        self.assertEqual(
            item["ascendant_name"],
            "records/items/lootaffixes/ascended/mastery/playerclass01/a301a.dbr",
        )
        self.assertEqual(item["ascendant_seed"], 0)

    def test_all_v1300_ascended_affixes_are_mapped(self):
        self.assertEqual(len(ASCENDED_AFFIX_RECORDS), 989)
        self.assertEqual(
            ASCENDED_AFFIX_RECORDS["aa16176"],
            "records/items/lootaffixes/ascended/mastery/playerclass02/a302c.dbr",
        )
        self.assertEqual(
            ASCENDED_AFFIX_RECORDS["aa16453"],
            "records/items/lootaffixes/ascended/mastery/playerclass05/a302b.dbr",
        )
        self.assertEqual(
            ASCENDED_AFFIX_RECORDS["aa17145"],
            "records/items/lootaffixes/ascended/ad318b.dbr",
        )

    def test_unknown_ascended_affix_is_rejected(self):
        import random

        with self.assertRaises(GenerationError):
            _blank_item(
                random.Random(1),
                {"item": "records/items/test.dbr", "ascendedAffix": "aa99999"},
            )

    def test_known_devotion_autocast_controller_is_bound(self):
        block = {"version": 8, "skills": [], "item_skills": []}
        warnings: list[str] = []
        _apply_skills(
            block,
            [
                {
                    "name": "records/skills/playerclass01/test.dbr",
                    "level": 1,
                    "autoCastSkill": (
                        "records/skills/devotion/tier2_09f_skill.dbr"
                    ),
                }
            ],
            warnings,
        )
        self.assertEqual(
            block["skills"][0]["autocast_controller_name"],
            "records/controllers/itemskills/cast_@enemyonattack_25%.dbr",
        )
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
