"""Backward-compatible entry point for the canonical CLI.

Existing shortcuts may continue to call ``python book_week.py --headless``.
All behavior now lives in :mod:`asimut_booker.cli`.
"""

from __future__ import annotations

import sys

from asimut_booker.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["run", *sys.argv[1:]]))
