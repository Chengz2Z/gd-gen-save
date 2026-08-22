"""External JSON language-pack loader used by the desktop GUI."""

from __future__ import annotations

import json
import locale
import os
from pathlib import Path
import sys
from typing import Any


DEFAULT_LANGUAGE = "zh_CN"


def _program_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _bundled_directory() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


class LanguageManager:
    """Load editable packs beside the executable with a bundled fallback."""

    def __init__(self, config_file: Path | None = None) -> None:
        self.config_file = config_file
        self.packs: dict[str, dict[str, Any]] = {}
        self._load_directory(_bundled_directory() / "languages")
        self.fallback_pack = self.packs.get(DEFAULT_LANGUAGE, {"strings": {}})
        external = _program_directory() / "languages"
        if external.resolve() != (_bundled_directory() / "languages").resolve():
            self._load_directory(external)

        requested = os.environ.get("GENERATESAVE_LANG") or self._configured_language()
        if not requested:
            system_language = (locale.getlocale()[0] or "").replace("-", "_")
            requested = system_language if system_language in self.packs else DEFAULT_LANGUAGE
        self.language = requested if requested in self.packs else DEFAULT_LANGUAGE

    def _load_directory(self, directory: Path) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                meta = data.get("_meta", {})
                code = str(meta.get("code") or path.stem)
                strings = data.get("strings")
                if isinstance(strings, dict):
                    self.packs[code] = data
            except (OSError, UnicodeError, json.JSONDecodeError):
                # A user-edited pack must not prevent the application from starting.
                continue

    def _configured_language(self) -> str:
        if not self.config_file:
            return ""
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
            return str(data.get("language", ""))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return ""

    def text(self, key: str, **values: object) -> str:
        fallback = self.fallback_pack.get("strings", {})
        current = self.packs.get(self.language, {}).get("strings", {})
        value = current.get(key, fallback.get(key, key))
        try:
            return str(value).format(**values)
        except (KeyError, IndexError, ValueError):
            return str(value)

    def language_choices(self) -> list[tuple[str, str]]:
        choices = []
        for code, pack in self.packs.items():
            name = str(pack.get("_meta", {}).get("name") or code)
            choices.append((code, name))
        return sorted(choices, key=lambda item: item[1].casefold())

    def set_language(self, code: str) -> bool:
        if code not in self.packs:
            return False
        self.language = code
        return True

    def crafting_name(self, path: str, default: str) -> str:
        fallback = self.fallback_pack.get("crafting_bonuses", {})
        current = self.packs.get(self.language, {}).get("crafting_bonuses", {})
        return str(current.get(path, fallback.get(path, default)))

    def message(self, value: object) -> str:
        """Translate a static message emitted by the generation core."""
        source = str(value)
        current = self.packs.get(self.language, {}).get("messages", {})
        return str(current.get(source, source))
