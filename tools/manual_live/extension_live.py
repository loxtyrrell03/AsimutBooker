"""Local inspection helper for tracked extension state.

The former version contained an independent horizon model and could directly
edit reservations. Those paths are retired: real extension checks must go
through ``book_week.py --extensions-only`` so a fresh live room policy, agenda,
mutation receipt, and exact reload verification are always present.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app_settings import SettingsError, update_settings
from book_week import load_extendable_bookings, settings_file


def list_extendable_bookings() -> int:
    """Print recorded state only; historical horizons are not current policy."""

    bookings = load_extendable_bookings()
    if not bookings:
        print("No extendable bookings are currently tracked.")
        return 0

    print("Tracked extendable bookings (read-only local state):")
    for index, booking in enumerate(bookings, 1):
        horizon = booking.get("horizon_minutes")
        if horizon is None and booking.get("horizon_days") is not None:
            horizon = booking["horizon_days"] * 24 * 60
        print(
            f"{index}. {booking['date']} {booking['room']} "
            f"{booking['startTime']}-{booking['endTime']} -> "
            f"{booking['target_end']} (recorded horizon: {horizon} minutes)"
        )
    print("Run-time eligibility still requires a new complete Asimut observation.")
    return 0


def clear_extendable_bookings() -> int:
    """Clear local tracking only when the operator explicitly requests it."""

    def mutate(settings):
        entries = settings.get("extendable_bookings", [])
        if not isinstance(entries, list):
            raise SettingsError("extendable_bookings must be a JSON list")
        count = len(entries)
        settings["extendable_bookings"] = []
        return count

    count = update_settings(mutate, settings_file)
    print(f"Cleared {count} locally tracked extendable booking(s).")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect or explicitly clear local extension tracking state"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="Read local state only")
    mode.add_argument(
        "--clear",
        action="store_true",
        help="Clear all local extension tracking state",
    )
    args = parser.parse_args(argv)
    try:
        if args.list:
            return list_extendable_bookings()
        return clear_extendable_bookings()
    except SettingsError as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
