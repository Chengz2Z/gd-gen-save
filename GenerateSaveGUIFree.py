#!/usr/bin/env python3
"""Portable GUI entry point that intentionally skips license validation."""

from GenerateSaveGUI import FREE_APP_TITLE, main


if __name__ == "__main__":
    raise SystemExit(main(require_license=False, window_title=FREE_APP_TITLE))
