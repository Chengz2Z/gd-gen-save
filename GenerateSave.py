#!/usr/bin/env python3
"""Generate a Grim Dawn character save from a GrimTools calculator link."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from generator import GenerationError, generate_save
from grimtools import GrimToolsError, fetch_build
from save_format import SaveFormatError
from license_manager import LicenseError, install_license, validate_license


TOOL_DIRECTORY = Path(__file__).resolve().parent


def ensure_activated() -> bool:
    status = validate_license()
    if status.valid:
        return True
    print(f"[授权] {status.reason}", file=sys.stderr)
    print(f"[授权] 本机机器码：{status.machine_code}", file=sys.stderr)
    try:
        value = input("请输入作者签发的许可证文件路径（直接回车取消）：").strip().strip('"')
        if not value:
            return False
        install_license(Path(value))
    except (EOFError, KeyboardInterrupt):
        print("\n[授权] 已取消导入。", file=sys.stderr)
        return False
    except (LicenseError, OSError) as exc:
        print(f"[授权] 许可证导入失败：{exc}", file=sys.stderr)
        return False
    print("[授权] 许可证导入成功。")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="以 _template 为模板，从 GrimTools 构筑链接生成可导入的角色存档。"
    )
    parser.add_argument("link", help="GrimTools 构筑链接或构筑 ID")
    parser.add_argument("--name", "-n", required=True, help="生成后的角色名称")
    parser.add_argument(
        "--template",
        type=Path,
        default=TOOL_DIRECTORY / "_template",
        help="模板角色目录（默认：工具目录下的 _template）",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=TOOL_DIRECTORY / "output",
        help="输出根目录（默认：工具目录下的 output）",
    )
    parser.add_argument(
        "--force", action="store_true", help="覆盖同名的已生成角色目录"
    )
    parser.add_argument(
        "--gender",
        choices=["male", "female"],
        default="male",
        help="角色性别（默认：male）",
    )
    parser.add_argument(
        "--no-materials",
        action="store_true",
        help="清空背包中的材料类物品",
    )
    parser.add_argument(
        "--no-iron",
        action="store_true",
        help="随机生成5位数铁币（不使用模板铁币数）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if not ensure_activated():
        return 2
    args = build_parser().parse_args(argv)
    try:
        print("[1/4] 正在读取 GrimTools 构筑……")
        build = fetch_build(args.link)
        print(
            f"      构筑 ID: {build.build_id}；游戏版本: {build.game_version}"
        )
        print("[2/4] 正在复制模板并写入角色数据……")
        result = generate_save(
            build,
            args.name,
            args.template,
            args.output,
            overwrite=args.force,
            male=(args.gender == "male"),
            keep_materials=not args.no_materials,
            keep_iron=not args.no_iron,
        )
        print("[3/4] 已完成解密后回读校验。")
        print("[4/4] 角色存档生成成功：")
        print(f"      角色名称: {result.character_name}")
        print(f"      职业标记: {result.class_tag}")
        print(f"      等级: {result.level}")
        print(
            f"      装备: {result.equipment_count}；"
            f"技能: {result.skill_count}；星座节点: {result.devotion_count}"
        )
        print(f"      输出目录: {result.output_directory}")
        for warning in result.warnings:
            print(f"[注意] {warning}")
        return 0
    except (GenerationError, GrimToolsError, SaveFormatError, OSError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
