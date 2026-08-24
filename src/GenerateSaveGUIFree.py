#!/usr/bin/env python3
"""Portable GUI entry point that intentionally skips license validation."""

from GenerateSaveGUI import APP_FREE_TITLE_AND_AUTHOR, main


if __name__ == "__main__":
    raise SystemExit(main(require_license=False, window_title=APP_FREE_TITLE_AND_AUTHOR))
