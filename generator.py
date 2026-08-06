"""Apply a GrimTools build to the bundled ``_template`` save template."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import random
import re
import shutil

from grimtools import GrimToolsBuild
from save_format import Block3Data, CharacterSave, SaveFormatError

DATABASE_DIR = Path(__file__).resolve().parent / "database"
DEVOTION_CONFIG_FILE = DATABASE_DIR / "devotion_config.json"


EQUIPMENT_SLOTS = {
    "head": ("equipment", 0),
    "amulet": ("equipment", 1),
    "chest": ("equipment", 2),
    "legs": ("equipment", 3),
    "feet": ("equipment", 4),
    "hands": ("equipment", 5),
    "ring1": ("equipment", 6),
    "ring2": ("equipment", 7),
    "waist": ("equipment", 8),
    "shoulders": ("equipment", 9),
    "medal": ("equipment", 10),
    "relic": ("equipment", 11),
    "weapon1": ("weapon", 0, 0),
    "weapon2": ("weapon", 0, 1),
    "weapon1Alt": ("weapon", 1, 0),
    "weapon2Alt": ("weapon", 1, 1),
}


# GrimTools' expanded build endpoint currently leaves ascended affixes as its
# compact ``aaNNNNN`` identifiers, while player.gdc stores database records.
# The following compact description expands to all 989 affixes in GD 1.3.0.0.
# Writing the compact id creates an unknown affix, and the integer following
# this field is not a random seed (non-zero values make current saves invalid).
def _load_devotion_config() -> dict[str, dict]:
    """从外部JSON文件加载星座技能最大等级和经验值配置"""
    if not DEVOTION_CONFIG_FILE.exists():
        return {}
    try:
        with open(DEVOTION_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _build_ascended_affix_records() -> dict[str, str]:
    records: dict[str, str] = {}

    def add(compact_id: int, record: str) -> int:
        records[f"aa{compact_id}"] = record
        return compact_id + 1

    compact_id = 16015
    general_defensive = (
        (301, "ab"), (302, "ab"), (303, "ab"), (304, "b"),
        (305, "ab"), (306, "ab"), (307, "ab"), (308, "ab"),
        (309, "ab"), (310, "ab"), (311, "ab"), (312, "ab"),
        (313, "ab"), (314, "a"), (315, "ab"), (316, "ab"),
    )
    for number, suffixes in general_defensive:
        for suffix in suffixes:
            compact_id = add(
                compact_id,
                f"records/items/lootaffixes/ascended/ad{number}{suffix}.dbr",
            )
    for number in range(301, 309):
        for suffix in "abcd":
            compact_id = add(
                compact_id,
                f"records/items/lootaffixes/ascended/ao{number}{suffix}.dbr",
            )

    mastery_a_suffixes = {
        1: ("abcd", "abcd", "abcd", "abcdg", "abcdg", "abcdefg"),
        2: ("abcd", "abcd", "abcd", "abcd", "abcdg", "abcd"),
        3: ("abcd", "abcd", "abcd", "abcd", "abcd", "abcd"),
        4: ("abcd", "abcd", "abcdefg", "abcdefg", "abcdefgh", "abcdefg"),
        5: ("abcd", "abcd", "abcd", "abcd", "abcd", "abcd"),
        6: ("abcd", "abcd", "abcd", "abcd", "abcdgh", "abcdgh"),
        7: ("abcd", "abcd", "abcd", "abcdefgh", "abcdefgh", "abcdefgh"),
        8: ("abcd", "abcd", "abcd", "abcd", "abcdg", "abcdg"),
        9: ("abcd", "abcd", "abcd", "abcd", "abcdg", "abcdefg"),
        10: ("abcd", "abcd", "abcd", "abcd", "abcdg", "abcdg"),
    }
    for mastery in range(1, 11):
        directory = (
            "records/items/lootaffixes/ascended/mastery/"
            f"playerclass{mastery:02d}/"
        )
        for offset, suffixes in enumerate(mastery_a_suffixes[mastery]):
            for suffix in suffixes:
                compact_id = add(
                    compact_id, f"{directory}a{301 + offset}{suffix}.dbr"
                )
        for number in range(301, 309):
            for suffix in "abcdefgh":
                compact_id = add(
                    compact_id, f"{directory}b{number}{suffix}.dbr"
                )

    records.update({
        "aa17142": "records/items/lootaffixes/ascended/ad304a.dbr",
        "aa17143": "records/items/lootaffixes/ascended/ad314b.dbr",
        "aa17144": "records/items/lootaffixes/ascended/ad317b.dbr",
        "aa17145": "records/items/lootaffixes/ascended/ad318b.dbr",
    })
    if compact_id != 17000 or len(records) != 989:
        raise RuntimeError("飞升词缀映射表构造失败")
    return records


ASCENDED_AFFIX_RECORDS = _build_ascended_affix_records()


INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class GenerationError(ValueError):
    pass


@dataclass(frozen=True)
class GenerationResult:
    output_directory: Path
    player_file: Path
    character_name: str
    build_id: str
    game_version: str
    class_tag: str
    level: int
    equipment_count: int
    skill_count: int
    devotion_count: int
    warnings: tuple[str, ...]


def validate_character_name(name: str) -> str:
    value = name.strip()
    if not value:
        raise GenerationError("角色名称不能为空")
    if value in {".", ".."} or INVALID_NAME_CHARS.search(value):
        raise GenerationError("角色名称包含 Windows 文件名不允许的字符")
    if value.endswith((" ", ".")):
        raise GenerationError("角色名称不能以空格或句点结尾")
    utf16_units = len(value.encode("utf-16-le")) // 2
    if utf16_units > 32:
        raise GenerationError("角色名称过长，最多允许 32 个 UTF-16 字符")
    return value


def _class_tag(masteries: list[str]) -> str:
    if not masteries:
        return ""
    normalized = sorted({str(value).zfill(2) for value in masteries}, key=int)
    return "tagSkillClassName" + "".join(normalized)


def _seed_rng(build: GrimToolsBuild, character_name: str) -> random.Random:
    import time
    timestamp = str(time.time())
    digest = hashlib.sha256(
        f"{build.build_id}\0{character_name}\0{timestamp}".encode("utf-8")
    ).digest()
    return random.Random(int.from_bytes(digest[:16], "little"))


def _ascended_affix_record(value: object) -> str:
    affix = str(value or "")
    if not affix or affix.startswith("records/"):
        return affix
    try:
        return ASCENDED_AFFIX_RECORDS[affix]
    except KeyError as exc:
        raise GenerationError(
            f"GrimTools 飞升词缀 {affix} 尚无存档记录映射；"
            "已停止生成，以免产生游戏无法识别或缺少词缀的存档。"
        ) from exc


def _blank_item(rng: random.Random, item_data: dict | None = None) -> dict:
    data = item_data or {}
    component = str(data.get("component", ""))
    augment = str(data.get("augment", ""))
    ascendant = _ascended_affix_record(data.get("ascendedAffix", ""))
    return {
        "basename": str(data.get("item", "")),
        "prefix_name": str(data.get("prefix", "")),
        "suffix_name": str(data.get("suffix", "")),
        "modifier_name": "",
        "transmute_name": "",
        "seed": rng.randrange(1, 0x7FFFFFFF),
        "relic_name": component,
        "relic_bonus": str(data.get("relicBonus", "")),
        "relic_seed": rng.randrange(1, 0x7FFFFFFF) if component else 0,
        "augment_name": augment,
        "unknown": 0,
        "augment_seed": rng.randrange(1, 0x7FFFFFFF) if augment else 0,
        "ascendant_name": ascendant,
        "ascendant_seed": 0,
        "relic_completion_level": 0,
        "stack_count": 1,
        "v11_fields": [0, 0],
        "attached": bool(data.get("item")),
    }


def _apply_equipment(
    block3: Block3Data, equipment: dict, rng: random.Random, warnings: list[str]
) -> int:
    if not block3.has_data or not isinstance(block3.tail, dict):
        raise GenerationError("模板存档没有可写入的装备数据")

    for slot, location in EQUIPMENT_SLOTS.items():
        item = _blank_item(rng, equipment.get(slot))
        if location[0] == "equipment":
            block3.tail["equipment"][location[1]] = item
        else:
            block3.tail["weapon_sets"][location[1]]["items"][location[2]] = item

    main_weapon = equipment.get("weapon1", {}).get("item", "")
    if "/melee2h/" in main_weapon or "/ranged2h/" in main_weapon:
        block3.tail["weapon_sets"][0]["items"][1] = _blank_item(rng)

    if any(item.get("ascendedAffix") for item in equipment.values()):
        warnings.append(
            "构筑包含飞升词缀；已写入词缀记录，但随机品质字段仍采用默认值。"
        )
    equipped = block3.tail["equipment"] + [
        item
        for weapon_set in block3.tail["weapon_sets"]
        for item in weapon_set["items"]
    ]
    return sum(1 for item in equipped if item.get("basename"))


def _skill_prototype_map(skill_block: dict) -> dict[str, dict]:
    return {
        skill["skill_name"]: deepcopy(skill)
        for skill in skill_block["skills"]
    }


def _blank_skill(name: str, level: int, block_version: int) -> dict:
    skill = {
        "skill_name": name,
        "level": max(0, int(level)),
        "enabled": True,
        "devotion_level": 1 if name.startswith("records/skills/devotion/") else 0,
        "devotion_experience": 0,
        "sublevel": 0,
        "active": False,
        "transition": False,
        "autocast_skill_name": "",
        "autocast_controller_name": "",
    }
    if block_version >= 8:
        skill["v8_field"] = 0
    return skill


def _apply_skills(skill_block: dict, build_skills: list[dict], warnings: list[str]):
    prototypes = _skill_prototype_map(skill_block)
    defaults = [
        deepcopy(skill)
        for skill in skill_block["skills"]
        if skill["skill_name"].startswith("records/skills/default/")
    ]
    controller_map = {
        skill["autocast_skill_name"]: skill["autocast_controller_name"]
        for skill in skill_block["skills"]
        if skill["autocast_skill_name"] and skill["autocast_controller_name"]
    }

    # 加载星座技能配置
    devotion_config = _load_devotion_config()
    
    generated: list[dict] = []
    for definition in build_skills:
        name = str(definition.get("name", ""))
        if not name:
            continue
        level = int(definition.get("level", 1))
        skill = deepcopy(prototypes.get(name)) or _blank_skill(
            name, level, skill_block["version"]
        )
        skill["level"] = level
        if name.startswith("records/skills/devotion/"):
            skill["devotion_level"] = max(1, skill.get("devotion_level", 1))
            # 从配置文件中查找星座技能的最大等级和经验值
            if name in devotion_config:
                config = devotion_config[name]
                skill["devotion_level"] = config.get("devotion_level", skill["devotion_level"])
                skill["devotion_experience"] = config.get("devotion_experience", skill.get("devotion_experience", 0))
        autocast = str(definition.get("autoCastSkill", ""))
        skill["autocast_skill_name"] = autocast
        if autocast:
            controller = controller_map.get(autocast, "")
            skill["autocast_controller_name"] = controller
        else:
            skill["autocast_controller_name"] = ""
        generated.append(skill)

    skill_block["skills"] = defaults + generated
    skill_block["item_skills"] = []
    devotion_count = sum(
        1
        for skill in generated
        if skill["skill_name"].startswith("records/skills/devotion/")
        and skill["level"] > 0
    )
    return len(generated), devotion_count


def _clean_hotslots(save: CharacterSave) -> None:
    block = save.block(14).payload
    valid = {
        skill["skill_name"] for skill in save.block(8).payload["skills"]
    }
    for index, slot in enumerate(block["hotslots"]):
        if index < 20:
            block["hotslots"][index] = {"type": -1}
        elif slot.get("type") == 0 and slot.get("skill_name") not in valid:
            block["hotslots"][index] = {"type": -1}


def apply_build(
    save: CharacterSave, build: GrimToolsBuild, character_name: str
) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    data = build.data
    bio = data.get("bio", {})
    level = int(bio.get("level", save.header.character_level))
    if level != save.header.character_level:
        raise GenerationError(
            f"当前模板为 {save.header.character_level} 级，但构筑为 {level} 级；"
            "为避免经验值不一致，当前版本只生成与模板同等级的角色。"
        )

    save.header.character_name = character_name
    save.header.character_level = level
    class_tag = _class_tag(build.masteries)
    if class_tag:
        save.header.player_class_name = class_tag

    bio_block = save.block(2).payload
    bio_block["level"] = level
    bio_block["attribute_points"] = int(bio.get("attributePoints", 0))
    bio_block["skill_points"] = int(bio.get("skillPoints", 0))
    bio_block["devotion_points"] = int(bio.get("devotionPoints", 0))
    for source, target in (
        ("physique", "physique"),
        ("cunning", "cunning"),
        ("spirit", "spirit"),
    ):
        if source in bio:
            bio_block[target] = float(bio[source])

    rng = _seed_rng(build, character_name)
    equipment_count = _apply_equipment(
        save.block(3).payload, data.get("equipment", {}), rng, warnings
    )
    skill_count, devotion_count = _apply_skills(
        save.block(8).payload, data.get("skills", []), warnings
    )
    bio_block["total_devotion_points_unlocked"] = devotion_count

    if data.get("itemSkills"):
        warnings.append("构筑包含物品自动技能；装备会赋予技能，但快捷栏未自动配置。")
    if data.get("transformSkills"):
        warnings.append("构筑包含技能变形配置；当前版本未单独写入变形状态。")
    _clean_hotslots(save)
    save.block(16).payload["max_level"] = max(
        level, int(save.block(16).payload["max_level"])
    )
    return {
        "class_tag": save.header.player_class_name,
        "level": level,
        "equipment_count": equipment_count,
        "skill_count": skill_count,
        "devotion_count": devotion_count,
    }, warnings


def generate_save(
    build: GrimToolsBuild,
    character_name: str,
    template_directory: Path,
    output_root: Path,
    overwrite: bool = False,
) -> GenerationResult:
    name = validate_character_name(character_name)
    template = template_directory.resolve()
    if not (template / "player.gdc").is_file():
        raise GenerationError(f"模板目录缺少 player.gdc：{template}")

    output_root = output_root.resolve()
    output_directory = output_root / f"_{name}"
    if output_root not in output_directory.parents:
        raise GenerationError("输出角色目录必须位于指定输出根目录内")
    if output_directory.exists():
        if not overwrite:
            raise GenerationError(
                f"输出目录已经存在：{output_directory}；使用 --force 可覆盖"
            )
        shutil.rmtree(output_directory)
    output_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, output_directory)

    try:
        save = CharacterSave.load(output_directory / "player.gdc")
        summary, warnings = apply_build(save, build, name)
        generated = save.to_bytes()
        verified = CharacterSave.from_bytes(generated)
        if verified.header.character_name != name:
            raise SaveFormatError("生成后角色名称回读不一致")

        for filename in (
            "player.gdc", "player.gdc.bak", "player.g00", "player.g01", "player.g02"
        ):
            path = output_directory / filename
            if path.exists() or filename == "player.gdc":
                path.write_bytes(generated)
    except Exception:
        shutil.rmtree(output_directory, ignore_errors=True)
        raise

    return GenerationResult(
        output_directory=output_directory,
        player_file=output_directory / "player.gdc",
        character_name=name,
        build_id=build.build_id,
        game_version=build.game_version,
        warnings=tuple(warnings),
        **summary,
    )
