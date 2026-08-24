"""Public-key validation for author-issued, machine-bound licenses."""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


PRODUCT_ID = "grim-dawn-archive-generator"
LICENSE_VERSION = 2
LICENSE_HEADER = b"GDAG-LICENSE-2\n"
PUBLIC_KEY = base64.b64decode("vQ4g8LPGIFFM8t0HzkcrTvGCnnWbpHt1QMbCU+H4Fec=")
_MACHINE_CODE_PATTERN = re.compile(r"^[0-9A-F]{32}$")


class LicenseError(RuntimeError):
    """A license is absent, invalid, corrupted, or belongs to another PC."""


@dataclass(frozen=True)
class LicenseStatus:
    valid: bool
    reason: str = ""
    issued_at: str = ""
    machine_code: str = ""


def default_license_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / "GrimDawnArchiveGenerator" / "license.lic"


def _machine_fingerprint() -> str:
    if sys.platform != "win32":
        raise LicenseError("离线授权仅支持 Windows。")
    try:
        import winreg

        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            access,
        ) as key:
            machine_guid = str(winreg.QueryValueEx(key, "MachineGuid")[0]).strip()
    except OSError as exc:
        raise LicenseError("无法读取 Windows 机器标识。") from exc

    system_drive = os.environ.get("SystemDrive", "C:") + "\\"
    serial = wintypes.DWORD()
    maximum_component = wintypes.DWORD()
    flags = wintypes.DWORD()
    get_volume_information = ctypes.WinDLL("kernel32", use_last_error=True).GetVolumeInformationW
    get_volume_information.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    get_volume_information.restype = wintypes.BOOL
    if not get_volume_information(
        system_drive,
        None,
        0,
        ctypes.byref(serial),
        ctypes.byref(maximum_component),
        ctypes.byref(flags),
        None,
        0,
    ):
        raise LicenseError("无法读取系统盘机器标识。")

    identity = f"{machine_guid.lower()}|{serial.value:08x}|{PRODUCT_ID}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest().upper()


def machine_code() -> str:
    # 128-bit code is short enough to transfer while retaining ample collision resistance.
    compact = _machine_fingerprint()[:32]
    return "-".join(compact[index : index + 4] for index in range(0, 32, 4))


def normalize_machine_code(value: str) -> str:
    compact = re.sub(r"[-\s]", "", value).upper()
    if not _MACHINE_CODE_PATTERN.fullmatch(compact):
        raise LicenseError("机器码格式无效，应为 32 位十六进制字符。")
    return "-".join(compact[index : index + 4] for index in range(0, 32, 4))


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "ascii"
    )


def encode_license(claims: dict[str, object], signature: bytes) -> bytes:
    envelope = {
        "claims": claims,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return LICENSE_HEADER + base64.b64encode(canonical_json(envelope)) + b"\n"


def _decode_license(raw: bytes) -> tuple[dict[str, object], bytes]:
    if not raw.startswith(LICENSE_HEADER):
        raise LicenseError("许可证格式或版本无效。")
    try:
        payload_text = raw[len(LICENSE_HEADER) :].strip()
        payload = base64.b64decode(payload_text, validate=True)
        if base64.b64encode(payload) != payload_text:
            raise ValueError("non-canonical base64")
        envelope = json.loads(payload.decode("ascii"))
        claims = envelope["claims"]
        signature_text = str(envelope["signature"]).encode("ascii")
        signature = base64.b64decode(signature_text, validate=True)
        if base64.b64encode(signature) != signature_text:
            raise ValueError("non-canonical signature")
        if not isinstance(claims, dict):
            raise TypeError
        return claims, signature
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise LicenseError("许可证内容损坏。") from exc


def validate_license(path: Path | None = None) -> LicenseStatus:
    source = path or default_license_path()
    current_code = _safe_machine_code()
    if not source.is_file():
        return LicenseStatus(False, "未找到本机许可证。", machine_code=current_code)
    try:
        claims, signature = _decode_license(source.read_bytes())
        if claims.get("version") != LICENSE_VERSION or claims.get("product") != PRODUCT_ID:
            raise LicenseError("许可证版本或产品标识无效。")
        licensed_code = normalize_machine_code(str(claims.get("machine_code", "")))
        if not hmac.compare_digest(licensed_code, current_code):
            raise LicenseError("许可证与当前机器不匹配。")
        try:
            Ed25519PublicKey.from_public_bytes(PUBLIC_KEY).verify(
                signature, canonical_json(claims)
            )
        except InvalidSignature as exc:
            raise LicenseError("许可证数字签名校验失败。") from exc
        return LicenseStatus(
            True,
            issued_at=str(claims.get("issued_at", "")),
            machine_code=current_code,
        )
    except (OSError, LicenseError) as exc:
        return LicenseStatus(False, str(exc), machine_code=current_code)


def install_license(source: Path) -> LicenseStatus:
    status = validate_license(source)
    if not status.valid:
        raise LicenseError(status.reason)
    destination = default_license_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        temporary = destination.with_name(destination.name + ".tmp")
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    return status


def _safe_machine_code() -> str:
    try:
        return machine_code()
    except LicenseError:
        return "不可用"
