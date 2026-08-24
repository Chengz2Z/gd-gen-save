#!/usr/bin/env python3
"""Author-only CLI for issuing a license to a user-provided machine code.

This module contains the signing private key.  Possession of this file grants
license-issuing authority: never distribute it or add it to the end-user build.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from pathlib import Path
import secrets
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from license_manager import (
    LICENSE_VERSION,
    PRODUCT_ID,
    LicenseError,
    canonical_json,
    encode_license,
    normalize_machine_code,
)


_PRIVATE_KEY = base64.b64decode("FVX/XltAO8B4oWk1r+qxzRSK0HbO6KCtI0ZxWpf12Zs=")


def issue_license(machine: str, output: Path) -> Path:
    normalized_code = normalize_machine_code(machine)
    signing_key = Ed25519PrivateKey.from_private_bytes(_PRIVATE_KEY)
    claims: dict[str, object] = {
        "version": LICENSE_VERSION,
        "product": PRODUCT_ID,
        "machine_code": normalized_code,
        "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "license_id": secrets.token_hex(16),
    }
    signature = signing_key.sign(canonical_json(claims))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_bytes(encode_license(claims, signature))
    temporary.replace(output)
    return output.resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="为指定机器码签发离线许可证。")
    parser.add_argument("machine_code", help="用户提供的 32 位分组机器码")
    parser.add_argument("--output", "-o", type=Path, help="许可证输出路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code = normalize_machine_code(args.machine_code)
        output = args.output or PROJECT_DIR / "artifacts" / "licenses" / f"GDAG-{code}.lic"
        path = issue_license(code, output)
    except (OSError, LicenseError) as exc:
        print(f"签发失败：{exc}", file=sys.stderr)
        return 2
    print(f"许可证已生成：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
