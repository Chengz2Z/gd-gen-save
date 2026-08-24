"""Resolve writable paths for source and packaged GUI runs."""

from __future__ import annotations

from pathlib import Path
import sys


def program_directory() -> Path:
    """Return the EXE directory, or an isolated runtime directory in source mode."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1] / "artifacts" / "runtime"


def gui_config_path() -> Path:
    """Return the editable GUI configuration path beside the packaged program."""
    return program_directory() / "config" / "config.json"
