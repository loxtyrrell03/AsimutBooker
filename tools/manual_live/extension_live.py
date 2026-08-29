"""Manual booking-extension diagnostic with safe and dangerous modes.

This script tests the extension workflow in isolation:
1. Manually add a fake "extendable booking" to settings.json
2. Run the extension logic to see if it can find and edit the booking

Usage:
    python tools/manual_live/extension_live.py --dry-run
    python tools/manual_live/extension_live.py --list
    python tools/manual_live/extension_live.py  # DANGEROUS: may edit booking

``--add-test`` and ``--clear`` mutate local settings. ``--headless`` changes
only browser visibility and is not a safety control. See README.md before use.
"""

import json
import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import functions from book_week
from book_week import (
    load_extendable_bookings,
    save_extendable_booking,
    remove_extendable_booking,
    calculate_max_extension,
    edit_reservation_end_time,
    try_extend_booking,
    ROOM_HORIZONS,
    DEFAULT_HORIZON,
    MAX_BOOKING_HOURS,
    MIN_BOOKING_MINUTES,
)

state_file = REPO_ROOT / "data" / "browser_state" / "state.json"
settings_file = REPO_ROOT / "data" / "settings.json"


def test_calculate_max_extension():
    """Test the calculate_max_extension function with various scenarios."""
    print("\n" + "="*60)
    print("TESTING: calculate_max_extension()")
    print("="*60)

    now = datetime.now()
    room = "B0.29"  # 5-day horizon
    date_str = now.strftime('%Y-%m-%d')

    # Scenario 1: Booking made 15 minutes ago (should allow 15 min extension)
    print("\n--- Scenario 1: Booking made 15 min ago ---")
    created_15_min_ago = (now - timedelta(minutes=15)).isoformat()
    can_extend, max_end, reason = calculate_max_extension(
        room, date_str,
        start_hour=9.0,           # 9:00 AM
        current_end_hour=9.5,     # Currently 9:30
        booking_timestamp=created_15_min_ago,
        target_end_hour=11.0      # Want 11:00
    )
    print(f"  Can extend: {can_extend}")
    print(f"  Max end: {max_end}")
    print(f"  Reason: {reason}")

    # Scenario 2: Booking made 45 minutes ago (should allow 45 min extension)
    print("\n--- Scenario 2: Booking made 45 min ago ---")
    created_45_min_ago = (now - timedelta(minutes=45)).isoformat()
    can_extend, max_end, reason = calculate_max_extension(
        room, date_str,
        start_hour=9.0,
        current_end_hour=9.5,
        booking_timestamp=created_45_min_ago,
        target_end_hour=11.0
    )
    print(f"  Can extend: {can_extend}")
    print(f"  Max end: {max_end}")
    print(f"  Reason: {reason}")

    # Scenario 3: Booking made 2 hours ago (should be at max)
    print("\n--- Scenario 3: Booking made 2 hours ago ---")
    created_2_hr_ago = (now - timedelta(hours=2)).isoformat()
    can_extend, max_end, reason = calculate_max_extension(
        room, date_str,
        start_hour=9.0,
        current_end_hour=9.5,
        booking_timestamp=created_2_hr_ago,
        target_end_hour=11.0
    )
    print(f"  Can extend: {can_extend}")
    print(f"  Max end: {max_end}")
    print(f"  Reason: {reason}")

    # Scenario 4: Booking already at target
    print("\n--- Scenario 4: Already at target ---")
    can_extend, max_end, reason = calculate_max_extension(
        room, date_str,
        start_hour=9.0,
        current_end_hour=11.0,    # Already at target
        booking_timestamp=created_45_min_ago,
        target_end_hour=11.0
    )
    print(f"  Can extend: {can_extend}")
    print(f"  Max end: {max_end}")
    print(f"  Reason: {reason}")

    # Scenario 5: Just created (< 15 min ago)
    print("\n--- Scenario 5: Just created (5 min ago) ---")
    created_5_min_ago = (now - timedelta(minutes=5)).isoformat()
    can_extend, max_end, reason = calculate_max_extension(
        room, date_str,
        start_hour=9.0,
        current_end_hour=9.5,
        booking_timestamp=created_5_min_ago,
        target_end_hour=11.0
    )
    print(f"  Can extend: {can_extend}")
    print(f"  Max end: {max_end}")
    print(f"  Reason: {reason}")


def list_extendable_bookings():
    """List all current extendable bookings."""
    print("\n" + "="*60)
    print("CURRENT EXTENDABLE BOOKINGS")
    print("="*60)

    bookings = load_extendable_bookings()
    if not bookings:
        print("  No extendable bookings found.")
        return []

    for i, b in enumerate(bookings):
        print(f"\n  [{i+1}] {b['room']} on {b['date']}")
        print(f"      Current: {b['startTime']} - {b['endTime']}")
        print(f"      Target:  {b['startTime']} - {b['target_end']}")
        print(f"      Created: {b['created_at']}")

        # Calculate if extension is possible
        start_parts = b['startTime'].split(':')
        start_hour = int(start_parts[0]) + int(start_parts[1]) / 60
        end_parts = b['endTime'].split(':')
        current_end_hour = int(end_parts[0]) + int(end_parts[1]) / 60
        target_parts = b['target_end'].split(':')
        target_end_hour = int(target_parts[0]) + int(target_parts[1]) / 60

        can_extend, max_end, reason = calculate_max_extension(
            b['room'], b['date'], start_hour, current_end_hour,
            b['created_at'], target_end_hour
        )
        print(f"      Can extend: {can_extend} - {reason}")

    return bookings


def add_test_booking():
    """Add a fake extendable booking for testing."""
    print("\n" + "="*60)
    print("ADDING TEST BOOKING")
    print("="*60)

    # Create a booking that looks like it was made 20 minutes ago
    now = datetime.now()
    fake_created = (now - timedelta(minutes=20)).isoformat()

    # Use tomorrow's date to avoid conflicts with real bookings
    tomorrow = (now + timedelta(days=1)).date()

    test_booking = {
        "date": tomorrow.strftime('%Y-%m-%d'),
        "startTime": "09:00",
        "endTime": "09:30",
        "room": "B0.29",
        "created_at": fake_created,
        "target_end": "11:00",
        "horizon_days": 5
    }

    # Load settings and add booking
    settings = {}
    if settings_file.exists():
        with open(settings_file, 'r') as f:
            settings = json.load(f)

    if "extendable_bookings" not in settings:
        settings["extendable_bookings"] = []

    settings["extendable_bookings"].append(test_booking)

    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=2)

    print(f"  Added test booking: {test_booking['room']} {test_booking['date']} {test_booking['startTime']}-{test_booking['endTime']}")
    print(f"  (Simulated as created 20 min ago)")

    return test_booking


def clear_test_bookings():
    """Remove all extendable bookings."""
    print("\n" + "="*60)
    print("CLEARING EXTENDABLE BOOKINGS")
    print("="*60)

    if not settings_file.exists():
        print("  No settings file found.")
        return

    with open(settings_file, 'r') as f:
        settings = json.load(f)

    count = len(settings.get("extendable_bookings", []))
    settings["extendable_bookings"] = []

    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=2)

    print(f"  Cleared {count} extendable booking(s).")


def test_extension_ui(headless=False):
    """Test the actual UI extension workflow."""
    print("\n" + "="*60)
    print("TESTING: Extension UI Workflow")
    print("="*60)

    bookings = load_extendable_bookings()
    if not bookings:
        print("  No extendable bookings to test.")
        print("  Run with --add-test first to add a test booking.")
        return

    # Use the first booking
    booking = bookings[0]
    print(f"\n  Testing with: {booking['room']} {booking['date']} {booking['startTime']}")

    # Check if extension is possible
    start_parts = booking['startTime'].split(':')
    start_hour = int(start_parts[0]) + int(start_parts[1]) / 60
    end_parts = booking['endTime'].split(':')
    current_end_hour = int(end_parts[0]) + int(end_parts[1]) / 60
    target_parts = booking['target_end'].split(':')
    target_end_hour = int(target_parts[0]) + int(target_parts[1]) / 60

    can_extend, max_end, reason = calculate_max_extension(
        booking['room'], booking['date'], start_hour, current_end_hour,
        booking['created_at'], target_end_hour
    )

    if not can_extend:
        print(f"  Cannot extend: {reason}")
        return

    new_end_h = int(max_end)
    new_end_m = int((max_end % 1) * 60)
    new_end_time = f"{new_end_h:02d}:{new_end_m:02d}"
    print(f"  Will attempt to extend to: {new_end_time}")

    # Launch browser and test
    print("\n  Launching browser...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            storage_state=str(state_file),
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        print("  Calling try_extend_booking()...")
        success, result_end, message = try_extend_booking(page, booking)

        print(f"\n  Result:")
        print(f"    Success: {success}")
        print(f"    New end: {result_end}")
        print(f"    Message: {message}")

        if not headless:
            print("\n  Keeping browser open for 10 seconds...")
            page.wait_for_timeout(10000)

        context.close()
        browser.close()


def main():
    parser = argparse.ArgumentParser(description="Test booking extension feature")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--dry-run", action="store_true", help="Only test calculations, don't use browser")
    parser.add_argument("--add-test", action="store_true", help="Add a fake test booking")
    parser.add_argument("--clear", action="store_true", help="Clear all extendable bookings")
    parser.add_argument("--list", action="store_true", help="List current extendable bookings")
    args = parser.parse_args()

    print("="*60)
    print("BOOKING EXTENSION TEST")
    print("="*60)
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if args.clear:
        clear_test_bookings()
        return

    if args.add_test:
        add_test_booking()

    if args.list:
        list_extendable_bookings()
        return

    # Always run calculation tests
    test_calculate_max_extension()

    # List current bookings
    list_extendable_bookings()

    # Run UI test unless dry-run
    if not args.dry_run:
        test_extension_ui(headless=args.headless)
    else:
        print("\n  [DRY RUN] Skipping UI test.")

    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60)


if __name__ == "__main__":
    main()
