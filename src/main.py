#!/usr/bin/env python3
"""Compatibility entry point for the authoritative ``book_week`` runtime.

The original module-based implementation had its own room list and advance
window configuration. Keeping a second mutation-capable runtime would let stale
policy bypass the fresh Asimut catalog, so legacy commands are translated to
the canonical fail-closed entry point instead.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def build_legacy_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Legacy AsimutBooker command wrapper; room policy is always "
            "refreshed by book_week.py"
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--setup", action="store_true", help="Open visible login setup")
    mode.add_argument("--once", action="store_true", help="Run one canonical pass")
    mode.add_argument(
        "--schedule",
        action="store_true",
        help="Retired; use the verified Windows scheduled task",
    )
    parser.add_argument("--headed", action="store_true", help="Show Chromium")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Map the one-off run to canonical --check-only",
    )
    parser.add_argument(
        "--config",
        help="Retired; the canonical rules-only config path is fixed",
    )
    return parser


def translate_legacy_args(
    argv: Sequence[str] | None = None,
) -> tuple[list[str] | None, str | None]:
    """Translate a safe legacy invocation or return a fail-closed message."""

    parser = build_legacy_parser()
    args = parser.parse_args(argv)
    if args.config:
        return None, (
            "--config is retired. Use config/config.yaml; room lists and horizons "
            "cannot be overridden."
        )
    if args.schedule:
        return None, (
            "The legacy in-process scheduler is retired. Install or repair "
            "AsimutBooker_Recurring from the GUI or setup_scheduled_tasks.ps1."
        )
    if args.setup:
        if args.dry_run:
            return None, "--dry-run cannot be combined with --setup."
        return ["--setup-login"], None

    translated: list[str] = []
    if not args.headed:
        translated.append("--headless")
    if args.dry_run:
        translated.append("--check-only")
    return translated, None


def main(argv: Sequence[str] | None = None) -> int:
    """Run only the canonical implementation; never instantiate legacy policy."""

    translated, error = translate_legacy_args(argv)
    if error:
        print(f"ERROR: {error}")
        return 2

    import book_week

    print("Using the canonical live-policy AsimutBooker runtime.")
    return int(book_week.main(translated) or 0)


if __name__ == "__main__":
    sys.exit(main())
