"""Book practice rooms for the entire week (up to 7 days ahead).

This script applies all RWCMD booking rules:
- Rolling quota of 28 bookings
- Maximum 2hr peak allowance (Mon-Fri 9am-4pm)
- Minimum 30 min, maximum 2hr per booking
- 60-minute gap between same room bookings
- Room-specific booking horizons (3, 5, or 7 days in advance)

Usage:
    python book_week.py              # Run with visible browser
    python book_week.py --headless   # Run without browser window (for scheduled tasks)
"""

import re
import sys
import json
import time
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

state_file = Path("data/browser_state/state.json")
history_file = Path("data/booking_history.json")
settings_file = Path("data/settings.json")

# ntfy.sh notification settings
# Change this to your own unique topic name (like "asimut-yourname-secret123")
NTFY_TOPIC = "asimut-loxty"
NTFY_ENABLED = True  # Set to False to disable notifications

# Default values (used as fallback if config.yaml is missing or invalid)
_DEFAULT_CONFIG = {
    'rolling_quota': 28,
    'peak_start': 9,
    'peak_end': 16,
    'max_peak_hours': 2,
    'same_room_gap_minutes': 60,
    'priority_rooms': [
        "B0.29", "B1.09", "B0.11", "B1.16", "B0.15", "B0.13", "B0.14", "B0.27",
        "B1.14", "B1.17", "A3.39", "B0.16", "B1.20", "B1.18", "B1.21", "B1.19",
        "B1.06", "B1.07", "B1.08", "B1.10", "B1.11", "B1.15",
        "B0.23"
    ],
    'room_horizons': {
        "B1.09": 3, "B1.16": 3,
        "B0.23": 5, "B0.24": 5, "B0.27": 5, "B0.29": 5,
        "B1.06": 5, "B1.07": 5, "B1.08": 5, "B1.10": 5, "B1.11": 5,
        "B1.14": 5, "B1.15": 5, "B1.17": 5, "B1.18": 5, "B1.19": 5,
        "B1.20": 5, "B1.21": 5,
        "A3.39": 7, "B0.16": 7,
    },
    'default_horizon': 7,
}


def load_config():
    """Load configuration from config.yaml with fallback to hardcoded defaults."""
    config_file = Path("config/config.yaml")

    if not config_file.exists():
        return _DEFAULT_CONFIG.copy()

    if not YAML_AVAILABLE:
        print("Warning: PyYAML not installed, using default configuration")
        return _DEFAULT_CONFIG.copy()

    try:
        with open(config_file, 'r') as f:
            cfg = yaml.safe_load(f)

        result = _DEFAULT_CONFIG.copy()

        # Load rules
        if cfg and 'rules' in cfg:
            rules = cfg['rules']
            result['rolling_quota'] = rules.get('rolling_quota', _DEFAULT_CONFIG['rolling_quota'])
            result['same_room_gap_minutes'] = rules.get('same_room_gap_minutes', _DEFAULT_CONFIG['same_room_gap_minutes'])
            if 'peak_hours' in rules:
                peak = rules['peak_hours']
                result['max_peak_hours'] = peak.get('max_hours', _DEFAULT_CONFIG['max_peak_hours'])
                if 'start' in peak:
                    result['peak_start'] = int(peak['start'].split(':')[0])
                if 'end' in peak:
                    result['peak_end'] = int(peak['end'].split(':')[0])

        # Load rooms from config
        if cfg and 'rooms' in cfg and 'priority' in cfg['rooms']:
            rooms = cfg['rooms']['priority']
            result['priority_rooms'] = [r['room'] for r in rooms]
            # Start with default horizons, then apply config overrides
            result['room_horizons'] = _DEFAULT_CONFIG['room_horizons'].copy()
            for r in rooms:
                if 'horizon' in r:
                    result['room_horizons'][r['room']] = r['horizon']

        return result
    except Exception as e:
        print(f"Warning: Could not load config.yaml ({e}), using defaults")
        return _DEFAULT_CONFIG.copy()


# Load config and set module-level constants
_CONFIG = load_config()
MAX_ROLLING_QUOTA_HOURS = _CONFIG['rolling_quota']
MAX_BOOKING_HOURS = 2
MIN_BOOKING_MINUTES = 30
PREFERRED_MIN_MINUTES = 60  # Prefer slots at least 1 hour
PEAK_START = _CONFIG['peak_start']
PEAK_END = _CONFIG['peak_end']
MAX_PEAK_HOURS = _CONFIG['max_peak_hours']
SAME_ROOM_GAP_MINUTES = _CONFIG['same_room_gap_minutes']
DEFAULT_BOOKINGS_PER_DAY = 3  # Default limit per day, dynamically adjusted based on enabled days
PRIORITY_ROOMS = _CONFIG['priority_rooms']
ROOM_HORIZONS = _CONFIG['room_horizons']
DEFAULT_HORIZON = _CONFIG['default_horizon']


def wait_until_target_time(target_time_str, page=None):
    """Wait until the specified time (HH:MM format) before proceeding.

    Keeps browser session alive with periodic small interactions.
    """
    target_hour, target_minute = map(int, target_time_str.split(':'))
    now = datetime.now()
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

    # If target time already passed, proceed immediately
    if now >= target:
        print(f"Target time {target_time_str} already passed, proceeding immediately")
        return

    wait_seconds = (target - now).total_seconds()
    print(f"\n{'='*60}")
    print(f"READY - Waiting for target time: {target_time_str}")
    print(f"{'='*60}")
    print(f"Current time: {now.strftime('%H:%M:%S')}")
    print(f"Seconds to wait: {wait_seconds:.0f}")

    last_log_second = -1
    last_keepalive_second = -1

    # Wait with periodic status updates and session keepalive
    while datetime.now() < target:
        remaining = (target - datetime.now()).total_seconds()
        if remaining <= 0:
            break

        remaining_int = int(remaining)

        # Log countdown every 10 seconds (avoid duplicate logs)
        if remaining_int % 10 == 0 and remaining_int != last_log_second:
            print(f"  T-{remaining_int}s...")
            last_log_second = remaining_int

        # Keep browser session alive every 30 seconds
        if page and remaining_int % 30 == 0 and remaining_int != last_keepalive_second:
            try:
                page.evaluate("1")  # Minimal JS to keep session active
            except:
                pass
            last_keepalive_second = remaining_int

        time.sleep(0.5)

    print(f"\n>>> TARGET TIME REACHED: {datetime.now().strftime('%H:%M:%S')} <<<")
    print(f"{'='*60}\n")


def is_room_available_to_book(room, target_date, slot_start_hour):
    """
    Check if a room is available to book based on its horizon.
    Rooms become available exactly X days later, by the minute.

    Args:
        room: Room name (e.g., "B1.09")
        target_date: The date we want to book (datetime object)
        slot_start_hour: The start hour of the slot (e.g., 9.5 for 9:30)

    Returns:
        Tuple of (is_available, reason_if_not)
    """
    horizon_days = ROOM_HORIZONS.get(room, DEFAULT_HORIZON)
    now = datetime.now()

    # Calculate when this slot becomes available
    # The slot becomes bookable exactly horizon_days before the slot time
    # Handle both date and datetime objects
    if hasattr(target_date, 'date'):
        target_date_obj = target_date.date()
    else:
        target_date_obj = target_date
    slot_datetime = datetime.combine(target_date_obj, datetime.min.time())
    slot_datetime = slot_datetime.replace(
        hour=int(slot_start_hour),
        minute=int((slot_start_hour % 1) * 60)
    )

    # When does this slot become available?
    available_from = slot_datetime - timedelta(days=horizon_days)

    if now < available_from:
        time_until = available_from - now
        hours_until = time_until.total_seconds() / 3600
        if hours_until < 1:
            mins_until = int(time_until.total_seconds() / 60)
            return False, f"Available in {mins_until}min (horizon: {horizon_days}d)"
        elif hours_until < 24:
            return False, f"Available in {hours_until:.1f}h (horizon: {horizon_days}d)"
        else:
            days_until = hours_until / 24
            return False, f"Available in {days_until:.1f}d (horizon: {horizon_days}d)"

    return True, ""


# Booking window opens at 7:30 AM
BOOKING_WINDOW_OPEN_HOUR = 7.5  # 7:30 AM


def is_any_room_bookable_on_day(target_date):
    """
    Check if ANY room from PRIORITY_ROOMS is bookable on a given day.

    Rooms become bookable at 7:30 AM on their horizon day. If no rooms
    are bookable yet (e.g., it's midnight and we're checking 7 days ahead),
    we should skip that day to avoid wasted navigation.

    Args:
        target_date: The date to check (date or datetime object)

    Returns:
        Tuple of (any_bookable, reason_if_none)
    """
    # Normalize to date object
    if hasattr(target_date, 'date'):
        target_date_obj = target_date.date()
    else:
        target_date_obj = target_date

    # Convert to datetime for is_room_available_to_book
    target_datetime = datetime.combine(target_date_obj, datetime.min.time())

    # Check if any priority room is bookable at the earliest slot time (7:30 AM)
    earliest_slot_hour = BOOKING_WINDOW_OPEN_HOUR

    for room in PRIORITY_ROOMS:
        is_available, _ = is_room_available_to_book(room, target_datetime, earliest_slot_hour)
        if is_available:
            return True, ""

    # Find the shortest wait time across all rooms
    min_wait = None
    for room in PRIORITY_ROOMS:
        horizon_days = ROOM_HORIZONS.get(room, DEFAULT_HORIZON)
        slot_datetime = datetime.combine(target_date_obj, datetime.min.time())
        slot_datetime = slot_datetime.replace(hour=7, minute=30)
        available_from = slot_datetime - timedelta(days=horizon_days)

        now = datetime.now()
        if now < available_from:
            wait = available_from - now
            if min_wait is None or wait < min_wait:
                min_wait = wait

    if min_wait:
        hours_until = min_wait.total_seconds() / 3600
        if hours_until < 1:
            mins_until = int(min_wait.total_seconds() / 60)
            return False, f"No rooms bookable yet (opens in {mins_until}min)"
        elif hours_until < 24:
            return False, f"No rooms bookable yet (opens in {hours_until:.1f}h)"
        else:
            days_until = hours_until / 24
            return False, f"No rooms bookable yet (opens in {days_until:.1f}d)"

    return False, "No rooms bookable"


def adjust_slot_for_peak_quota(slot_start, slot_end, target_date, remaining_peak_mins):
    """
    Adjust a slot to avoid the peak zone (9am-4pm) when peak quota is exceeded or limited.

    This handles cases like:
    - 8:00-9:30 with no peak quota → trim to 8:00-9:00
    - 15:30-17:00 with no peak quota → trim to 16:00-17:00
    - 10:00-14:00 with no peak quota → skip entirely (fully in peak)
    - 8:00-10:00 with 30min peak left → trim to 8:00-9:30

    Args:
        slot_start: Start hour (e.g., 8.0 for 8am)
        slot_end: End hour (e.g., 9.5 for 9:30am)
        target_date: The date of the slot
        remaining_peak_mins: How many peak minutes are still available

    Returns:
        Tuple of (adjusted_start, adjusted_end, was_adjusted, reason)
        If the slot should be skipped entirely, returns (None, None, True, reason)
    """
    # Only applies to weekdays
    if target_date.weekday() >= 5:  # Weekend
        return slot_start, slot_end, False, ""

    # Calculate how much of this slot is in peak hours
    overlap_start = max(slot_start, PEAK_START)
    overlap_end = min(slot_end, PEAK_END)
    peak_overlap_mins = max(0, (overlap_end - overlap_start) * 60)

    # If no peak overlap, no adjustment needed
    if peak_overlap_mins == 0:
        return slot_start, slot_end, False, ""

    # If we have enough peak quota for this slot, no adjustment needed
    if peak_overlap_mins <= remaining_peak_mins:
        return slot_start, slot_end, False, ""

    # We need to adjust. Check if slot is fully within peak hours
    if slot_start >= PEAK_START and slot_end <= PEAK_END:
        # Slot is fully in peak zone - can we use partial quota?
        if remaining_peak_mins >= MIN_BOOKING_MINUTES:
            # Use remaining quota: book for remaining_peak_mins
            new_end = slot_start + remaining_peak_mins / 60
            if new_end <= slot_end and (new_end - slot_start) * 60 >= MIN_BOOKING_MINUTES:
                return slot_start, new_end, True, f"Trimmed to use {remaining_peak_mins:.0f}min peak quota"
        return None, None, True, "Fully in peak zone, quota exceeded"

    # Slot crosses peak boundary - adjust to avoid peak zone
    adjusted_start = slot_start
    adjusted_end = slot_end

    # If slot starts before peak and ends during/after peak
    if slot_start < PEAK_START and slot_end > PEAK_START:
        if remaining_peak_mins <= 0:
            # No peak quota left - trim to end at peak start
            adjusted_end = PEAK_START
        else:
            # Use remaining peak quota
            max_end = PEAK_START + remaining_peak_mins / 60
            if max_end < slot_end:
                adjusted_end = min(max_end, slot_end)

    # If slot starts during peak and ends after peak
    if slot_start >= PEAK_START and slot_start < PEAK_END and slot_end > PEAK_END:
        if remaining_peak_mins <= 0:
            # No peak quota left - start at peak end
            adjusted_start = PEAK_END
        else:
            # Can we fit some peak time at the start?
            peak_portion = min(PEAK_END - slot_start, remaining_peak_mins / 60)
            if peak_portion * 60 < MIN_BOOKING_MINUTES:
                # Not enough for a booking during peak, start after peak
                adjusted_start = PEAK_END

    # Validate adjusted slot
    if adjusted_end - adjusted_start < MIN_BOOKING_MINUTES / 60:
        return None, None, True, "Adjusted slot too short"

    if adjusted_start != slot_start or adjusted_end != slot_end:
        return adjusted_start, adjusted_end, True, f"Adjusted from {slot_start:.2f}-{slot_end:.2f} to avoid peak"

    return slot_start, slot_end, False, ""


def adjust_slot_for_weekly_quota(slot_start, slot_end, remaining_quota_hours):
    """
    Adjust a slot to fit within the remaining weekly quota.

    If the slot duration exceeds the remaining quota, trim the slot to use
    the remaining quota (similar to peak quota trimming).

    Args:
        slot_start: Start hour (e.g., 8.0 for 8am)
        slot_end: End hour (e.g., 10.0 for 10am)
        remaining_quota_hours: How many hours are left in the weekly quota

    Returns:
        Tuple of (adjusted_start, adjusted_end, was_adjusted, reason)
        If the slot should be skipped entirely, returns (None, None, True, reason)
    """
    slot_duration_hours = slot_end - slot_start
    slot_duration_mins = slot_duration_hours * 60

    # If we have enough quota, no adjustment needed
    if slot_duration_hours <= remaining_quota_hours:
        return slot_start, slot_end, False, ""

    # Not enough quota - can we trim?
    remaining_quota_mins = remaining_quota_hours * 60

    if remaining_quota_mins < MIN_BOOKING_MINUTES:
        return None, None, True, f"Weekly quota too low ({remaining_quota_hours:.1f}h remaining)"

    # Trim the slot to fit the remaining quota
    new_end = slot_start + remaining_quota_hours
    if (new_end - slot_start) * 60 >= MIN_BOOKING_MINUTES:
        return slot_start, new_end, True, f"Trimmed to {remaining_quota_hours:.1f}h (weekly quota)"

    return None, None, True, f"Cannot fit in remaining weekly quota ({remaining_quota_hours:.1f}h)"


def load_disabled_dates():
    """Load list of disabled dates from settings file."""
    if settings_file.exists():
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                return set(settings.get("disabled_dates", []))
        except:
            pass
    return set()


def load_ignored_events():
    """Load list of ignored event keys from settings file.

    Ignored events are events the user wants to allow booking over
    (they won't be treated as conflicts).

    Returns:
        Set of event keys in format "YYYY-MM-DD_HH:MM_HH:MM"
    """
    if settings_file.exists():
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                return set(settings.get("ignored_events", []))
        except:
            pass
    return set()


def is_date_disabled(target_date, disabled_dates):
    """Check if a date is disabled for booking."""
    date_str = target_date.strftime('%Y-%m-%d')
    return date_str in disabled_dates


# Time preference presets: name -> (start_hour, end_hour)
TIME_PREFERENCE_PRESETS = {
    "morning": (7, 12),
    "afternoon": (12, 18),
    "evening": (18, 22),
    "afternoon_evening": (14, 22),
}


def load_time_preferences():
    """Load time preferences from settings file.

    Returns:
        dict with keys: enabled, start_hour, end_hour, strict_mode
        Hours are decimal (e.g., 14.5 = 14:30)
    """
    default = {"enabled": False, "start_hour": 14.0, "end_hour": 22.0, "strict_mode": False}

    if settings_file.exists():
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                prefs = settings.get("time_preferences", {})

                if not prefs.get("enabled", False):
                    return default

                # Get preset or custom hours
                preset = prefs.get("preset", "afternoon_evening")
                if preset in TIME_PREFERENCE_PRESETS:
                    start_hour, end_hour = TIME_PREFERENCE_PRESETS[preset]
                else:
                    # Custom preset - use saved hour:minute values, convert to decimal
                    start_h = prefs.get("custom_start_hour", prefs.get("custom_start", 14))
                    start_m = prefs.get("custom_start_min", 0)
                    end_h = prefs.get("custom_end_hour", prefs.get("custom_end", 22))
                    end_m = prefs.get("custom_end_min", 0)
                    start_hour = start_h + start_m / 60.0
                    end_hour = end_h + end_m / 60.0

                return {
                    "enabled": True,
                    "start_hour": start_hour,
                    "end_hour": end_hour,
                    "strict_mode": prefs.get("strict_mode", False)
                }
        except:
            pass

    return default


def load_booking_strategy():
    """Load booking strategy settings from settings file.

    Returns:
        dict with keys: reverse_date_order (bool)
    """
    default = {"reverse_date_order": False}

    if settings_file.exists():
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                strategy = settings.get("booking_strategy", {})
                return {
                    "reverse_date_order": strategy.get("reverse_date_order", False)
                }
        except:
            pass

    return default


# =============================================================================
# EXTENDABLE BOOKINGS TRACKING
# =============================================================================
# When booking at the horizon edge, only 30 min can be booked initially.
# These functions track such bookings so they can be extended later.

def normalize_time_string(time_str):
    """Normalize time string to HH:MM format.

    Handles variations like "9:30" -> "09:30"

    Args:
        time_str: Time string like "9:30" or "09:30"

    Returns:
        Normalized time string "HH:MM"
    """
    parts = time_str.split(':')
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return f"{hour:02d}:{minute:02d}"


def load_extendable_bookings():
    """Load bookings that may be extendable from settings.

    Returns:
        List of dicts with: date, startTime, endTime, room, created_at, target_end, horizon_days
    """
    if not settings_file.exists():
        return []
    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)
        bookings = settings.get("extendable_bookings", [])

        # Clean up expired entries (older than 7 days or past dates)
        now = datetime.now()
        today = now.date()
        valid_bookings = []
        required_fields = ["date", "startTime", "endTime", "room", "created_at", "target_end"]
        for b in bookings:
            try:
                # Validate all required fields exist
                missing_fields = [f for f in required_fields if f not in b]
                if missing_fields:
                    print(f"  [WARN] Skipping extendable booking with missing fields: {missing_fields}")
                    continue

                booking_date = datetime.strptime(b["date"], '%Y-%m-%d').date()
                created_at = datetime.fromisoformat(b["created_at"])
                # Keep if: date is today or future, and created within last 7 days
                if booking_date >= today and (now - created_at).total_seconds() / 86400 < 7:
                    valid_bookings.append(b)
            except Exception as e:
                print(f"  [WARN] Skipping invalid extendable booking: {e}")
                continue
        return valid_bookings
    except Exception as e:
        print(f"  [ERROR] Failed to load extendable bookings: {e}")
        return []


def save_extendable_booking(room, target_date, start_hour, end_hour, target_end_hour):
    """Save a booking as potentially extendable.

    Args:
        room: Room name (e.g., "B0.29")
        target_date: Date of the booking (datetime or date object)
        start_hour: Start time as decimal (e.g., 9.5 for 9:30)
        end_hour: Current end time as decimal
        target_end_hour: Target end time we wanted but couldn't book
    """
    # Format times
    if hasattr(target_date, 'strftime'):
        date_str = target_date.strftime('%Y-%m-%d')
    else:
        date_str = str(target_date)

    start_h = int(start_hour)
    start_m = int((start_hour % 1) * 60)
    end_h = int(end_hour)
    end_m = int((end_hour % 1) * 60)
    target_h = int(target_end_hour)
    target_m = int((target_end_hour % 1) * 60)

    booking = {
        "date": date_str,
        "startTime": f"{start_h:02d}:{start_m:02d}",
        "endTime": f"{end_h:02d}:{end_m:02d}",
        "room": room,
        "created_at": datetime.now().isoformat(),
        "target_end": f"{target_h:02d}:{target_m:02d}",
        "horizon_days": ROOM_HORIZONS.get(room, DEFAULT_HORIZON)
    }

    # Load existing settings
    settings = {}
    if settings_file.exists():
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)
        except:
            pass

    if "extendable_bookings" not in settings:
        settings["extendable_bookings"] = []

    # Check if this booking already exists (same room, date, start time)
    existing = [b for b in settings["extendable_bookings"]
                if b["date"] == booking["date"]
                and b["room"] == booking["room"]
                and b["startTime"] == booking["startTime"]]
    if existing:
        # Update the existing entry
        for b in settings["extendable_bookings"]:
            if (b["date"] == booking["date"] and b["room"] == booking["room"]
                and b["startTime"] == booking["startTime"]):
                b["endTime"] = booking["endTime"]
                b["target_end"] = booking["target_end"]
                break
    else:
        # Add new booking
        settings["extendable_bookings"].append(booking)

    # Clean up old entries (older than 7 days or past dates)
    now = datetime.now()
    today = now.date()
    settings["extendable_bookings"] = [
        b for b in settings["extendable_bookings"]
        if datetime.strptime(b["date"], '%Y-%m-%d').date() >= today
        and (now - datetime.fromisoformat(b["created_at"])).days < 7
    ]

    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=2)

    print(f"  [EXTEND] Saved for extension: {room} {date_str} {booking['startTime']}->{booking['target_end']}")


def remove_extendable_booking(room, date_str, start_time):
    """Remove a booking from extendable list after successful extension.

    Args:
        room: Room name
        date_str: Date string (YYYY-MM-DD)
        start_time: Start time string (HH:MM)
    """
    if not settings_file.exists():
        return

    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)
    except:
        return

    if "extendable_bookings" not in settings:
        return

    # Filter out the matching booking
    original_count = len(settings["extendable_bookings"])
    settings["extendable_bookings"] = [
        b for b in settings["extendable_bookings"]
        if not (b["date"] == date_str and b["room"] == room and b["startTime"] == start_time)
    ]

    if len(settings["extendable_bookings"]) < original_count:
        with open(settings_file, 'w') as f:
            json.dump(settings, f, indent=2)


def update_extendable_booking_end_time(room, date_str, start_time, new_end_time):
    """Update the end time of an extendable booking after successful extension.

    Args:
        room: Room name
        date_str: Date string (YYYY-MM-DD)
        start_time: Start time string (HH:MM)
        new_end_time: New end time string (HH:MM)

    Returns:
        bool: True if booking was found and updated, False otherwise
    """
    if not settings_file.exists():
        print(f"  [WARN] Settings file not found, cannot update extendable booking")
        return False

    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)
    except Exception as e:
        print(f"  [WARN] Failed to load settings: {e}")
        return False

    if "extendable_bookings" not in settings:
        print(f"  [WARN] No extendable_bookings in settings")
        return False

    # Normalize time strings for comparison (ensure HH:MM format)
    start_time_normalized = normalize_time_string(start_time)
    new_end_normalized = normalize_time_string(new_end_time)

    # Find and update the matching booking
    found = False
    for b in settings["extendable_bookings"]:
        b_start_normalized = normalize_time_string(b["startTime"])
        if b["date"] == date_str and b["room"] == room and b_start_normalized == start_time_normalized:
            b["endTime"] = new_end_normalized
            found = True
            # If we've reached the target, remove from list
            target_normalized = normalize_time_string(b["target_end"])
            if new_end_normalized == target_normalized:
                settings["extendable_bookings"] = [
                    x for x in settings["extendable_bookings"]
                    if not (x["date"] == date_str and x["room"] == room
                            and normalize_time_string(x["startTime"]) == start_time_normalized)
                ]
            break

    if not found:
        print(f"  [WARN] Could not find extendable booking to update: {room} {date_str} {start_time}")
        return False

    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=2)
    return True


def calculate_max_extension(room, date_str, start_hour, current_end_hour, booking_timestamp, target_end_hour):
    """Calculate maximum possible extension for a booking made at horizon edge.

    When booking at the exact horizon time, only MIN_BOOKING_MINUTES can be booked.
    As time passes, more of the slot becomes available for booking.

    Args:
        room: Room name (e.g., "B0.29")
        date_str: Date string (YYYY-MM-DD)
        start_hour: Start time as decimal (e.g., 9.0 for 9:00)
        current_end_hour: Current end time as decimal (e.g., 9.5 for 9:30)
        booking_timestamp: ISO format timestamp when booking was created
        target_end_hour: The end time we ultimately want (e.g., 11.0 for 11:00)

    Returns:
        Tuple of (can_extend: bool, max_end_hour: float, reason: str)
    """
    try:
        # Validate timestamp format (kept for compatibility with saved data)
        datetime.fromisoformat(booking_timestamp)
    except:
        return False, current_end_hour, "Invalid booking timestamp"

    now = datetime.now()
    horizon_days = ROOM_HORIZONS.get(room, DEFAULT_HORIZON)

    # Calculate when this slot became available (horizon edge)
    try:
        slot_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except:
        return False, current_end_hour, "Invalid booking date"

    slot_datetime = datetime.combine(slot_date, datetime.min.time())
    slot_datetime = slot_datetime.replace(
        hour=int(start_hour),
        minute=int((start_hour % 1) * 60)
    )
    available_from = slot_datetime - timedelta(days=horizon_days)

    # Time elapsed since the horizon edge (not since booking)
    time_since_available_mins = (now - available_from).total_seconds() / 60
    if time_since_available_mins < 0:
        return False, current_end_hour, f"Not yet in horizon window ({-time_since_available_mins:.0f}min early)"

    # Current duration in minutes
    current_duration_mins = (current_end_hour - start_hour) * 60

    # Max total duration available at this moment (capped at 2 hours)
    max_total_duration_mins = min(
        time_since_available_mins,
        MAX_BOOKING_HOURS * 60
    )

    # If horizon window hasn't advanced beyond current booking, no extension possible
    if max_total_duration_mins < current_duration_mins:
        return False, current_end_hour, "Horizon window hasn't advanced past current booking"

    # Calculate max end hour (total duration from start)
    max_end_hour = start_hour + max_total_duration_mins / 60

    # Also cap at target end (no point going beyond what we originally wanted)
    max_end_hour = min(max_end_hour, target_end_hour)

    # Round down to nearest 15-minute interval
    max_end_mins = int((max_end_hour % 1) * 60)
    max_end_mins = (max_end_mins // 15) * 15
    max_end_hour = int(max_end_hour) + max_end_mins / 60

    # Need at least 15 minutes of extension to be worth it
    extension_mins = (max_end_hour - current_end_hour) * 60
    if extension_mins < 15:
        return False, current_end_hour, f"Only {extension_mins:.0f}min extension possible (need 15min)"

    # Check if we've already reached the target
    if current_end_hour >= target_end_hour:
        return False, current_end_hour, "Already at target duration"

    return True, max_end_hour, f"Can extend to {int(max_end_hour):02d}:{int((max_end_hour % 1) * 60):02d}"


def is_preferred_time(start_hour, time_prefs):
    """Check if a slot's start time falls within the preferred range.

    Args:
        start_hour: Slot start time as decimal hour (e.g., 14.5 for 2:30 PM)
        time_prefs: Dict from load_time_preferences()

    Returns:
        True if the time is preferred (or if preferences are disabled)
    """
    if not time_prefs["enabled"]:
        return True  # All times preferred if disabled

    return time_prefs["start_hour"] <= start_hour < time_prefs["end_hour"]


# =============================================================================
# SMART SWAP FUNCTIONS (commented out for future use)
# =============================================================================
#
# def load_smart_swap_settings():
#     """Load smart swap settings from settings file."""
#     default = {"enabled": False}
#     if settings_file.exists():
#         try:
#             with open(settings_file, 'r') as f:
#                 settings = json.load(f)
#                 strategy = settings.get("booking_strategy", {})
#                 return {"enabled": strategy.get("smart_swap_enabled", False)}
#         except:
#             pass
#     return default
#
#
# def is_outside_preferred_time(start_hour, end_hour, time_prefs):
#     """Check if a reservation is outside the preferred time window."""
#     if not time_prefs["enabled"]:
#         return False
#     pref_start = time_prefs["start_hour"]
#     pref_end = time_prefs["end_hour"]
#     return end_hour <= pref_start or start_hour >= pref_end
#
#
# def get_swappable_reservations(reservations, time_prefs):
#     """Get list of reservations that are outside the preferred time window."""
#     if not time_prefs["enabled"]:
#         return []
#     swappable = []
#     for res in reservations:
#         start_parts = res['startTime'].split(':')
#         end_parts = res['endTime'].split(':')
#         start_hour = int(start_parts[0]) + int(start_parts[1]) / 60
#         end_hour = int(end_parts[0]) + int(end_parts[1]) / 60
#         if is_outside_preferred_time(start_hour, end_hour, time_prefs):
#             swappable.append({**res, 'start_hour': start_hour, 'end_hour': end_hour})
#     return swappable
#
#
# # def cancel_reservation(page, reservation):
#     """Cancel an existing reservation in Asimut.
#
#     Args:
#         page: Playwright page object
#         reservation: Dict with date, startTime, endTime, room
#
#     Returns:
#         True if successfully cancelled, False otherwise
#     """
#     date_str = reservation['date']
#     start_time = reservation['startTime']
#     end_time = reservation['endTime']
#     room = reservation.get('room', 'Unknown')
#
#     print(f"  Attempting to cancel: {room} on {date_str} at {start_time}-{end_time}")
#
#     try:
#         # Navigate to agenda page
#         page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
#         page.wait_for_timeout(2000)
#
#         # Scroll to find the event
#         max_scrolls = 20
#         event_found = False
#
#         for scroll in range(max_scrolls):
#             time_text = f"{start_time} - {end_time}"
#             event_cards = page.locator('.as-event-panel, app-as-event').all()
#
#             for card in event_cards:
#                 try:
#                     card_text = card.inner_text(timeout=1000)
#                     if time_text in card_text and 'Reservation' in card_text:
#                         if room and room in card_text:
#                             event_found = True
#                             print(f"  Found matching event card")
#
#                             more_btn = card.locator('button[data-cy="button_more"], .as-event-options-button').first
#                             if more_btn.count() > 0:
#                                 more_btn.click()
#                                 page.wait_for_timeout(500)
#
#                                 cancel_option = page.locator('mat-list-item:has(mat-icon:has-text("cancel"))').first
#                                 if cancel_option.count() == 0:
#                                     cancel_option = page.locator('button:has-text("Cancel booking"), [role="menuitem"]:has-text("Cancel")').first
#
#                                 if cancel_option.count() > 0 and cancel_option.is_visible():
#                                     cancel_option.click()
#                                     page.wait_for_timeout(1000)
#
#                                     confirm_btn = page.locator('button:has-text("Confirm"), button:has-text("Yes"), button:has-text("OK"), button:has-text("Delete")').first
#                                     if confirm_btn.count() > 0 and confirm_btn.is_visible():
#                                         confirm_btn.click()
#                                         page.wait_for_timeout(1000)
#
#                                     print(f"  Successfully cancelled reservation: {room} {date_str} {start_time}")
#                                     return True
#                                 else:
#                                     print(f"  Could not find 'Cancel booking' option in menu")
#                                     page.keyboard.press("Escape")
#                             else:
#                                 print(f"  Could not find more options button on event card")
#                             break
#                 except Exception as e:
#                     continue
#
#             if event_found:
#                 break
#
#             page.evaluate("window.scrollBy(0, 300)")
#             page.wait_for_timeout(300)
#
#         if not event_found:
#             print(f"  Could not find event card for {room} {date_str} {start_time}")
#             return False
#
#     except Exception as e:
#         print(f"  Error cancelling reservation: {e}")
#         return False
#
#     return False
# =============================================================================


# =============================================================================
# BOOKING EXTENSION FUNCTIONS
# =============================================================================

def edit_reservation_end_time(page, booking, new_end_time):
    """Edit an existing reservation to extend its end time.

    Uses Asimut's edit feature:
    1. Navigate to agenda
    2. Find the event using JavaScript (fast DOM search)
    3. Click more_vert button
    4. Click "Edit event"
    5. Update end time
    6. Click away to dismiss dropdown, then Save
    7. Navigate back to agenda

    Args:
        page: Playwright page object
        booking: Dict with date, startTime, endTime, room
        new_end_time: New end time string (e.g., "10:00")

    Returns:
        True if successfully edited, False otherwise
    """
    date_str = booking['date']
    start_time = normalize_time_string(booking['startTime'])
    current_end = booking['endTime']
    room = booking.get('room', 'Unknown')

    print(f"  Editing reservation: {room} on {date_str}")
    print(f"    Current: {start_time}-{current_end}")
    print(f"    New end: {new_end_time}")

    try:
        # 1. Navigate to agenda page
        page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
        page.wait_for_timeout(2000)

        # 2. Find the matching event card using JavaScript for efficiency
        def find_matching_panel():
            """Use JavaScript to search DOM for matching reservation panel by date, room, and time."""
            return page.evaluate("""(args) => {
                const { room, time, targetDate } = args;
                const panels = document.querySelectorAll('.as-event-panel');

                // Parse target date for comparison (YYYY-MM-DD format)
                const [targetYear, targetMonth, targetDay] = targetDate.split('-').map(Number);

                const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                                   'July', 'August', 'September', 'October', 'November', 'December'];

                // Helper to find which date section a panel belongs to
                function getPanelDate(panel) {
                    // Walk backwards through previous siblings and parent to find date header
                    let el = panel;
                    while (el) {
                        // Check previous siblings
                        let sibling = el.previousElementSibling;
                        while (sibling) {
                            const text = (sibling.textContent || '').trim();
                            // Match date headers like "Monday 3 February 2026"
                            const dateMatch = text.match(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\\s+(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)\\s+(\\d{4})$/i);
                            if (dateMatch) {
                                const day = parseInt(dateMatch[2]);
                                const month = monthNames.indexOf(dateMatch[3]);
                                const year = parseInt(dateMatch[4]);
                                return { year, month, day };
                            }
                            sibling = sibling.previousElementSibling;
                        }
                        // Move up to parent and continue
                        el = el.parentElement;
                    }
                    return null;
                }

                // Search from end (future events) to beginning
                for (let i = panels.length - 1; i >= 0; i--) {
                    const panel = panels[i];
                    const text = panel.innerText || '';

                    // Must be a Reservation
                    if (!text.includes('Reservation')) continue;

                    // Must match room (case insensitive)
                    if (!text.toLowerCase().includes(room.toLowerCase())) continue;

                    // Must match start time
                    const timeMatch = text.match(/(\\d{2}):(\\d{2})\\s*-\\s*(\\d{2}):(\\d{2})/);
                    if (!timeMatch) continue;

                    const startTime = timeMatch[1] + ':' + timeMatch[2];
                    if (startTime !== time) continue;

                    // Must match date
                    const panelDate = getPanelDate(panel);
                    if (!panelDate) {
                        console.log('Could not determine date for panel', i);
                        continue;
                    }

                    // Compare dates (month is 0-indexed from monthNames, so targetMonth-1)
                    if (panelDate.year !== targetYear ||
                        panelDate.month !== (targetMonth - 1) ||
                        panelDate.day !== targetDay) {
                        continue;
                    }

                    // Check if cancelled (strikethrough)
                    const allElements = panel.querySelectorAll('*');
                    let isCancelled = false;
                    for (const el of allElements) {
                        const style = window.getComputedStyle(el);
                        if (style.textDecoration.includes('line-through') ||
                            style.textDecorationLine.includes('line-through')) {
                            isCancelled = true;
                            break;
                        }
                    }
                    if (isCancelled) continue;

                    // Found a valid match
                    return i;
                }
                return -1;
            }""", {"room": room, "time": start_time, "targetDate": date_str})

        # First try without scrolling
        matching_index = find_matching_panel()

        # If not found, scroll down and try again (booking may be further in future)
        if matching_index < 0:
            print(f"    Not found in visible area, scrolling...")
            for scroll_attempt in range(10):
                page.evaluate("window.scrollBy(0, 2000)")
                page.wait_for_timeout(300)
                matching_index = find_matching_panel()
                if matching_index >= 0:
                    break

        if matching_index < 0:
            print(f"    Could not find event card for {room} {date_str} {start_time}")
            # Already on agenda page, no need to navigate
            return False

        # Get the panel and scroll it into view
        all_panels = page.locator('.as-event-panel').all()
        reservation_card = all_panels[matching_index]
        print(f"    Found matching event card (panel #{matching_index})")

        reservation_card.scroll_into_view_if_needed()
        page.wait_for_timeout(500)

        # 3. Click the more_vert button on this specific card
        more_btn = reservation_card.locator("button[data-cy='button_more']").first
        if more_btn.count() == 0:
            more_btn = reservation_card.locator(".as-event-options-button").first
        if more_btn.count() == 0:
            more_btn = reservation_card.locator("button:has(mat-icon:has-text('more_vert'))").first
        if more_btn.count() == 0:
            more_btn = reservation_card.locator("mat-icon:has-text('more_vert')").first

        if more_btn.count() > 0 and more_btn.is_visible():
            more_btn.click()
            page.wait_for_timeout(500)
        else:
            print(f"    Could not find more options button")
            # Navigate back to clean state
            page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
            return False

        # 4. Click "Edit event" from the menu
        edit_option = page.locator("mat-list-item").filter(has_text="Edit event").first
        if edit_option.count() == 0:
            edit_option = page.locator("mat-list-item:has(mat-icon:has-text('edit'))").first

        if edit_option.count() > 0 and edit_option.is_visible():
            edit_option.click()
            page.wait_for_timeout(1500)
        else:
            print(f"    Could not find 'Edit event' option")
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            # Navigate back to clean state
            page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
            return False

        # 5. Find the end time input and update it
        end_input = page.locator("#endDate").first
        if end_input.count() == 0:
            end_input = page.locator("input[data-cy='time-range-end-time']").first

        if end_input.count() > 0 and end_input.is_visible():
            # Click and fill the new time
            end_input.click()
            end_input.fill(new_end_time)
            page.wait_for_timeout(300)

            # Click on the right side of the page to dismiss any dropdown
            viewport = page.viewport_size
            if viewport:
                page.mouse.click(viewport['width'] - 100, viewport['height'] // 2)
            page.wait_for_timeout(500)

            # 6. Check for yellow warning boxes before saving
            # Look for warning/error/conflict elements that indicate extension isn't allowed
            warning_elements = page.locator(".warning, .error, [class*='conflict'], [class*='clash'], [class*='warning']").all()
            has_warning = False
            for warn in warning_elements:
                if warn.is_visible():
                    text = warn.text_content() or ""
                    if text.strip() and len(text.strip()) > 3:
                        print(f"    Warning found: {text.strip()[:80]}")
                        has_warning = True
                        break

            # Also check for disabled Save button (icon shows remove/block/error)
            save_btn = page.locator("button:has-text('Save')").first
            if save_btn.count() > 0 and save_btn.is_visible():
                save_icon = save_btn.locator("mat-icon").first
                if save_icon.count() > 0:
                    icon_text = save_icon.text_content() or ""
                    disabled_icons = ["remove", "block", "close", "cancel", "error", "warning"]
                    if any(disabled in icon_text.lower() for disabled in disabled_icons):
                        print(f"    Save disabled (icon: {icon_text})")
                        has_warning = True

            if has_warning:
                # Extension not allowed - cancel and return to agenda
                print(f"    Extension blocked by warning, cancelling...")
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
                return False

            # 7. Click Save button
            if save_btn.count() > 0 and save_btn.is_visible():
                save_btn.click()
                page.wait_for_timeout(2000)

                # 8. Navigate back to agenda
                page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")

                print(f"    SUCCESS: Extended to {new_end_time}")
                return True
            else:
                print(f"    Could not find Save button")
                # Press Escape and navigate back to clean state
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
                return False
        else:
            print(f"    Could not find end time input")
            # Press Escape and navigate back to clean state
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
            return False

    except Exception as e:
        print(f"    Error editing reservation: {e}")
        # Try to recover to a clean state
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
        except:
            pass
        return False

    return False


def try_extend_booking(page, booking, tracker=None):
    """Attempt to extend an existing booking using Asimut's edit feature.

    Args:
        page: Playwright page object
        booking: Dict from extendable_bookings with:
            date, startTime, endTime, room, created_at, target_end, horizon_days
        tracker: BookingTracker instance for quota/gap validation (optional)

    Returns:
        Tuple of (success: bool, new_end_time: str or None, message: str)
    """
    room = booking["room"]
    date_str = booking["date"]
    start_time = booking["startTime"]
    current_end = booking["endTime"]
    target_end = booking["target_end"]
    created_at = booking["created_at"]

    # Parse times to decimals
    start_parts = start_time.split(':')
    start_hour = int(start_parts[0]) + int(start_parts[1]) / 60

    end_parts = current_end.split(':')
    current_end_hour = int(end_parts[0]) + int(end_parts[1]) / 60

    target_parts = target_end.split(':')
    target_end_hour = int(target_parts[0]) + int(target_parts[1]) / 60

    # Calculate if extension is possible based on time elapsed
    can_extend, max_end_hour, reason = calculate_max_extension(
        room, date_str, start_hour, current_end_hour, created_at, target_end_hour
    )

    if not can_extend:
        return False, None, reason

    # Parse the booking date for tracker validation
    booking_date = datetime.strptime(date_str, '%Y-%m-%d')

    # If tracker is provided, validate against booking rules
    if tracker is not None:
        # Calculate the extension amount (what we're adding, not the total)
        extension_hours = max_end_hour - current_end_hour

        # Rule: Rolling weekly quota (28 hours per week)
        remaining_quota_hours = tracker.get_remaining_quota_hours()
        if extension_hours > remaining_quota_hours:
            if remaining_quota_hours < 0.25:  # Less than 15 minutes
                return False, None, f"Weekly quota full ({tracker.get_total_booking_hours():.1f}/{MAX_ROLLING_QUOTA_HOURS}h)"
            # Limit extension to stay within weekly quota
            max_end_hour = current_end_hour + remaining_quota_hours
            # Round down to 15-minute interval
            max_end_mins = int((max_end_hour % 1) * 60)
            max_end_mins = (max_end_mins // 15) * 15
            max_end_hour = int(max_end_hour) + max_end_mins / 60
            extension_hours = max_end_hour - current_end_hour
            if extension_hours < 0.25:  # Less than 15 minutes
                return False, None, f"Weekly quota would only allow {extension_hours * 60:.0f}min extension"

        # Rule: Peak hours limit (Mon-Fri 9am-4pm, max 2 hours per day)
        if booking_date.weekday() < 5:  # Monday-Friday
            # Calculate peak hours overlap for the EXTENSION portion only
            # (the current booking's peak hours are already accounted for)
            extension_peak_start = max(current_end_hour, PEAK_START)
            extension_peak_end = min(max_end_hour, PEAK_END)
            if extension_peak_end > extension_peak_start:
                new_peak_mins = (extension_peak_end - extension_peak_start) * 60
                day_peak_used = tracker.get_peak_used_for_day(booking_date)
                if day_peak_used + new_peak_mins > MAX_PEAK_HOURS * 60:
                    # Try to limit extension to stay within peak quota
                    remaining_peak_mins = MAX_PEAK_HOURS * 60 - day_peak_used
                    if remaining_peak_mins <= 0:
                        return False, None, f"Peak hours limit reached for {date_str} ({day_peak_used:.0f}/{MAX_PEAK_HOURS * 60} min)"

                    # Calculate max end hour that respects peak limit
                    # We can only extend into peak by remaining_peak_mins
                    max_peak_end = extension_peak_start + remaining_peak_mins / 60
                    if max_peak_end < max_end_hour and max_peak_end > current_end_hour:
                        # Limit extension to respect peak quota
                        max_end_hour = min(max_end_hour, max_peak_end)
                        # Round down to 15-minute interval
                        max_end_mins = int((max_end_hour % 1) * 60)
                        max_end_mins = (max_end_mins // 15) * 15
                        max_end_hour = int(max_end_hour) + max_end_mins / 60
                        # Check if extension is still worth it
                        if (max_end_hour - current_end_hour) * 60 < 15:
                            return False, None, f"Peak hours limit would only allow {(max_end_hour - current_end_hour) * 60:.0f}min extension"

        # Rule: Same-room gap (60 minutes between bookings in same room)
        # Check if extending would violate gap to another booking
        if date_str in tracker.reservation_ranges:
            for r_start, r_end, r_room in tracker.reservation_ranges[date_str]:
                if r_room and r_room == room:
                    # Skip if this is our own booking
                    if abs(r_start - start_hour) < 0.01:
                        continue
                    # Check if extension would violate gap before another booking
                    gap_before = (r_start - max_end_hour) * 60
                    if 0 < gap_before < SAME_ROOM_GAP_MINUTES:
                        # Limit extension to maintain gap
                        safe_end = r_start - SAME_ROOM_GAP_MINUTES / 60
                        if safe_end <= current_end_hour:
                            return False, None, f"Same-room gap: another booking at {int(r_start):02d}:{int((r_start % 1) * 60):02d}"
                        max_end_hour = safe_end
                        # Round down to 15-minute interval
                        max_end_mins = int((max_end_hour % 1) * 60)
                        max_end_mins = (max_end_mins // 15) * 15
                        max_end_hour = int(max_end_hour) + max_end_mins / 60
                        if (max_end_hour - current_end_hour) * 60 < 15:
                            return False, None, f"Same-room gap limits extension to <15min"

    # Format new end time
    new_end_h = int(max_end_hour)
    new_end_m = int((max_end_hour % 1) * 60)
    new_end_time = f"{new_end_h:02d}:{new_end_m:02d}"

    # Normalize target_end for comparison
    target_end_normalized = normalize_time_string(target_end)

    print(f"\n{'='*60}")
    print(f"EXTENDING BOOKING: {room} on {date_str}")
    print(f"  Current: {start_time} - {current_end}")
    print(f"  Target:  {start_time} - {new_end_time}")
    print(f"{'='*60}")

    # Perform the edit
    success = edit_reservation_end_time(page, booking, new_end_time)

    if success:
        # Update tracker with the extended hours
        if tracker is not None:
            # Add extension hours to existing reservation hours (for weekly quota)
            extension_hours = max_end_hour - current_end_hour
            tracker.existing_reservation_hours += extension_hours
            print(f"  [Tracker] Added {extension_hours:.2f}h extension to quota ({tracker.get_total_booking_hours():.1f}/{MAX_ROLLING_QUOTA_HOURS}h)")

            # Update peak hours if applicable
            if booking_date.weekday() < 5:
                extension_peak_start = max(current_end_hour, PEAK_START)
                extension_peak_end = min(max_end_hour, PEAK_END)
                if extension_peak_end > extension_peak_start:
                    new_peak_mins = (extension_peak_end - extension_peak_start) * 60
                    if date_str not in tracker.peak_hours_by_day:
                        tracker.peak_hours_by_day[date_str] = 0
                    tracker.peak_hours_by_day[date_str] += new_peak_mins

            # Update reservation_ranges with the new end time (for same-room gap calculations)
            if date_str in tracker.reservation_ranges:
                updated = False
                for i, (r_start, r_end, r_room) in enumerate(tracker.reservation_ranges[date_str]):
                    if r_room == room and abs(r_start - start_hour) < 0.01:
                        # Found the matching reservation, update its end time
                        tracker.reservation_ranges[date_str][i] = (r_start, max_end_hour, r_room)
                        updated = True
                        print(f"  [Tracker] Updated reservation_ranges: {room} now ends at {new_end_time}")
                        break
                if not updated:
                    # Reservation not found in ranges (shouldn't happen), add it
                    tracker.reservation_ranges[date_str].append((start_hour, max_end_hour, room))
                    print(f"  [Tracker] Added to reservation_ranges: {room} {start_time}-{new_end_time}")

        # Update or remove from extendable bookings
        if new_end_time == target_end_normalized:
            # Reached target, remove from tracking
            remove_extendable_booking(room, date_str, start_time)
        else:
            # Partially extended, update the end time
            update_extendable_booking_end_time(room, date_str, start_time, new_end_time)

        return True, new_end_time, "Extended successfully"

    return False, None, "Edit operation failed"


def calculate_max_bookings_per_day(remaining_quota_hours, enabled_days_count):
    """
    Calculate maximum bookings per day based on remaining quota and enabled days.

    This dynamically adjusts bookings per day so that if you have fewer days enabled,
    you can book more hours per day (up to the quota limit).

    Args:
        remaining_quota_hours: Hours left in the weekly quota (28h max)
        enabled_days_count: Number of days that are enabled for booking (1-8)

    Returns:
        Maximum bookings per day (integer, minimum 1, maximum based on quota)
    """
    if enabled_days_count <= 0:
        return 0

    # Calculate target hours per day
    hours_per_day = remaining_quota_hours / enabled_days_count

    # Each booking is up to MAX_BOOKING_HOURS (2 hours)
    # We want to spread bookings across the day, so calculate how many 2-hour blocks we need
    bookings_per_day = hours_per_day / MAX_BOOKING_HOURS

    # Round up to ensure we fill the quota, but at least 1 booking per day
    bookings_per_day = max(1, int(bookings_per_day + 0.5))

    # Cap at a reasonable maximum (e.g., 6 bookings = 12 hours per day max)
    # This prevents trying to book unreasonably many slots on a single day
    return min(bookings_per_day, 6)


def calculate_target_hours_for_day(target_date, enabled_dates, tracker, total_quota=MAX_ROLLING_QUOTA_HOURS):
    """
    Calculate target hours for a specific day to achieve even distribution.

    This function considers existing bookings across all enabled days and calculates
    how many more hours should be booked on the target day to achieve a balanced week.

    Args:
        target_date: The date to calculate target hours for
        enabled_dates: List of enabled dates in the booking window
        tracker: BookingTracker with existing reservation data
        total_quota: Total weekly quota (default 28 hours)

    Returns:
        Tuple of (target_hours, max_bookings) for the day
    """
    if not enabled_dates:
        return 0.0, 0

    # Calculate ideal hours per day for even distribution
    ideal_per_day = total_quota / len(enabled_dates)

    # Get current hours for each enabled day
    hours_per_day = {}
    total_existing = 0.0
    for d in enabled_dates:
        day_hours = tracker.get_hours_for_day(d)
        hours_per_day[d.strftime('%Y-%m-%d')] = day_hours
        total_existing += day_hours

    remaining_quota = max(0, total_quota - total_existing)

    # If no remaining quota, nothing to book
    if remaining_quota <= 0:
        return 0.0, 0

    # Count days that still need more hours (below ideal)
    days_needing_hours = []
    for d in enabled_dates:
        day_key = d.strftime('%Y-%m-%d')
        day_hours = hours_per_day.get(day_key, 0.0)
        if day_hours < ideal_per_day:
            # Calculate how much this day is "behind" the ideal
            deficit = ideal_per_day - day_hours
            days_needing_hours.append((d, deficit))

    target_date_key = target_date.strftime('%Y-%m-%d')
    current_hours = hours_per_day.get(target_date_key, 0.0)

    if not days_needing_hours:
        # All days are at or above ideal - distribute remaining evenly
        target_hours = remaining_quota / len(enabled_dates)
    elif current_hours >= ideal_per_day:
        # This day is already at or above ideal - don't add more
        # Let the days with deficits catch up first
        target_hours = 0.0
    else:
        # This day is below ideal - calculate its fair share of remaining quota
        # Prioritize days with larger deficits proportionally
        total_deficit = sum(deficit for _, deficit in days_needing_hours)
        day_deficit = ideal_per_day - current_hours
        # Proportional share based on how much this day needs relative to total need
        proportion = day_deficit / total_deficit if total_deficit > 0 else 1.0 / len(days_needing_hours)
        target_hours = min(remaining_quota * proportion, day_deficit)

    # Cap at remaining quota and ensure positive
    target_hours = max(0, min(target_hours, remaining_quota))

    # Convert to number of bookings (each up to MAX_BOOKING_HOURS)
    if target_hours <= 0:
        max_bookings = 0
    else:
        max_bookings = max(1, int((target_hours / MAX_BOOKING_HOURS) + 0.5))
        max_bookings = min(max_bookings, 6)  # Cap at 6 bookings per day

    return target_hours, max_bookings


class BookingTracker:
    """Tracks bookings and enforces rules."""

    def __init__(self):
        self.bookings = []  # List of (room, date, start_hour, end_hour)
        self.conflict_ranges = {}  # Per-day conflict ranges: {date: [(start, end), ...]}
        self.reservation_ranges = {}  # Per-day reservation ranges (for same-room gap buffer): {date: [(start, end), ...]}
        self.peak_hours_by_day = {}  # Per-day peak minutes: {date_key: minutes}
        self.reservation_hours_by_day = {}  # Per-day reservation hours: {date_key: hours}
        self.existing_reservation_hours = 0.0  # Total hours from existing reservations

    def add_existing_event(self, date, start_hour, end_hour, is_reservation=False, room=None):
        """Record an existing event from agenda scan.

        Args:
            date: The date of the event
            start_hour: Start time as decimal hour (e.g., 9.5 for 9:30)
            end_hour: End time as decimal hour
            is_reservation: True if this is a practice room booking ("Reservation"),
                           False for classes/rehearsals/other events
            room: Room name (e.g., "B0.27") if available, for same-room gap enforcement

        Only reservations (practice room bookings) count toward peak hours quota.
        All events are added as conflicts to avoid double-booking.
        """
        date_key = date.strftime('%Y-%m-%d')

        # ALL events block you from booking at the same time (you can't be in two places at once)
        # This includes both classes AND reservations
        if date_key not in self.conflict_ranges:
            self.conflict_ranges[date_key] = []
        self.conflict_ranges[date_key].append((start_hour, end_hour))

        # Also track reservation ranges with room info for same-room gap enforcement
        if is_reservation:
            if date_key not in self.reservation_ranges:
                self.reservation_ranges[date_key] = []
            self.reservation_ranges[date_key].append((start_hour, end_hour, room))

        # Only track peak hours for reservations (practice room bookings)
        peak_info = ""
        if is_reservation and date.weekday() < 5:  # Monday-Friday
            overlap_start = max(start_hour, PEAK_START)
            overlap_end = min(end_hour, PEAK_END)
            if overlap_end > overlap_start:
                peak_mins = (overlap_end - overlap_start) * 60
                if date_key not in self.peak_hours_by_day:
                    self.peak_hours_by_day[date_key] = 0
                self.peak_hours_by_day[date_key] += peak_mins
                peak_info = f" (+{peak_mins:.0f}min peak)"

        # Track reservation hours for rolling quota (28 hours per week)
        if is_reservation:
            duration_hours = end_hour - start_hour
            self.existing_reservation_hours += duration_hours
            # Also track per-day for smart redistribution
            if date_key not in self.reservation_hours_by_day:
                self.reservation_hours_by_day[date_key] = 0.0
            self.reservation_hours_by_day[date_key] += duration_hours

        event_type = "reservation" if is_reservation else "conflict"
        print(f"  [Tracker] Added {event_type}: {date_key} {start_hour:.2f}-{end_hour:.2f}{peak_info}")

    def add_booking(self, room, date, start_hour, end_hour):
        """Record a successful booking."""
        self.bookings.append((room, date, start_hour, end_hour))
        date_key = date.strftime('%Y-%m-%d')

        # Update peak hours if applicable (only bookings count, not existing events)
        peak_mins_added = 0
        if date.weekday() < 5:  # Monday-Friday
            overlap_start = max(start_hour, PEAK_START)
            overlap_end = min(end_hour, PEAK_END)
            if overlap_end > overlap_start:
                peak_mins_added = (overlap_end - overlap_start) * 60
                if date_key not in self.peak_hours_by_day:
                    self.peak_hours_by_day[date_key] = 0
                self.peak_hours_by_day[date_key] += peak_mins_added

        # Also add to conflict ranges so we don't double-book
        if date_key not in self.conflict_ranges:
            self.conflict_ranges[date_key] = []
        self.conflict_ranges[date_key].append((start_hour, end_hour))

        # Track per-day reservation hours for smart redistribution
        booking_hours = end_hour - start_hour
        if date_key not in self.reservation_hours_by_day:
            self.reservation_hours_by_day[date_key] = 0.0
        self.reservation_hours_by_day[date_key] += booking_hours

        print(f"  [Tracker] Added booking: {room} {date_key} {start_hour:.2f}-{end_hour:.2f}")
        day_peak = self.peak_hours_by_day.get(date_key, 0)
        day_hours = self.reservation_hours_by_day.get(date_key, 0)
        print(f"  [Tracker] Total bookings: {len(self.bookings)}, Day hours: {day_hours:.1f}h, Peak: {day_peak:.0f}/{MAX_PEAK_HOURS * 60} min")

    def add_conflict(self, date, start_hour, end_hour):
        """Record a discovered conflict range (during booking, not from agenda)."""
        date_key = date.strftime('%Y-%m-%d')
        if date_key not in self.conflict_ranges:
            self.conflict_ranges[date_key] = []
        self.conflict_ranges[date_key].append((start_hour, end_hour))
        print(f"  [Tracker] Added conflict: {date_key} {start_hour:.2f}-{end_hour:.2f}")

    def get_remaining_peak_minutes(self, date):
        """Get remaining peak minutes available for a specific date.

        Returns unlimited (9999) for weekends since there's no daily peak quota.
        """
        # No peak quota restriction on weekends
        if date.weekday() >= 5:  # Saturday=5, Sunday=6
            return 9999  # Effectively unlimited
        date_key = date.strftime('%Y-%m-%d')
        used = self.peak_hours_by_day.get(date_key, 0)
        return max(0, MAX_PEAK_HOURS * 60 - used)

    def get_peak_used_for_day(self, date):
        """Get peak minutes used for a specific date.

        Returns 0 for weekends since peak quota doesn't apply.
        """
        # No peak quota tracking on weekends
        if date.weekday() >= 5:  # Saturday=5, Sunday=6
            return 0
        date_key = date.strftime('%Y-%m-%d')
        return self.peak_hours_by_day.get(date_key, 0)

    def is_peak_quota_exceeded(self, date):
        """Check if peak hours quota is exceeded for a specific date.

        Always returns False for weekends since there's no daily peak quota.
        """
        # No peak quota restriction on weekends
        if date.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        date_key = date.strftime('%Y-%m-%d')
        used = self.peak_hours_by_day.get(date_key, 0)
        return used >= MAX_PEAK_HOURS * 60

    def get_total_booking_hours(self):
        """Get total booking hours (existing reservations + new bookings this session)."""
        new_booking_hours = sum(end - start for _, _, start, end in self.bookings)
        return self.existing_reservation_hours + new_booking_hours

    def get_hours_for_day(self, date):
        """Get total reservation hours for a specific day."""
        date_key = date.strftime('%Y-%m-%d')
        return self.reservation_hours_by_day.get(date_key, 0.0)

    def get_remaining_quota_hours(self):
        """Get remaining rolling quota hours."""
        return max(0, MAX_ROLLING_QUOTA_HOURS - self.get_total_booking_hours())

    def is_quota_full(self):
        """Check if rolling quota is exceeded."""
        return self.get_total_booking_hours() >= MAX_ROLLING_QUOTA_HOURS

    def overlaps_conflict(self, date, start_hour, end_hour):
        """Check if a time overlaps with known conflicts."""
        date_key = date.strftime('%Y-%m-%d')
        if date_key not in self.conflict_ranges:
            return False
        for c_start, c_end in self.conflict_ranges[date_key]:
            if start_hour < c_end and end_hour > c_start:
                return True
        return False

    def can_book(self, room, date, start_hour, duration_minutes):
        """Check if a booking is allowed by all rules."""
        end_hour = start_hour + duration_minutes / 60
        duration_hours = duration_minutes / 60

        # Rule: Min duration
        if duration_minutes < MIN_BOOKING_MINUTES:
            return False, "Duration too short"

        # Rule: Max duration
        if duration_minutes > MAX_BOOKING_HOURS * 60:
            return False, "Duration too long"

        # Rule: Rolling quota (28 hours per week)
        remaining_hours = self.get_remaining_quota_hours()
        if duration_hours > remaining_hours:
            return False, f"Exceeds weekly quota ({remaining_hours:.1f}h remaining)"

        # Rule: Check conflict ranges
        if self.overlaps_conflict(date, start_hour, end_hour):
            return False, "Overlaps with your existing reservation"

        # Rule: Peak hours limit (per day)
        if date.weekday() < 5:  # Weekday
            overlap_start = max(start_hour, PEAK_START)
            overlap_end = min(end_hour, PEAK_END)
            if overlap_end > overlap_start:
                new_peak = (overlap_end - overlap_start) * 60
                day_peak_used = self.get_peak_used_for_day(date)
                if day_peak_used + new_peak > MAX_PEAK_HOURS * 60:
                    return False, f"Peak hours limit for {date.strftime('%Y-%m-%d')} ({day_peak_used:.0f}/{MAX_PEAK_HOURS * 60} min used)"

        # Rule: Same room gap (must have 60min gap before AND after existing bookings)
        # Check against bookings made during this session
        for b_room, b_date, b_start, b_end in self.bookings:
            if b_room == room and b_date.date() == date.date():
                # Forward gap: new booking starts after existing ends
                gap_after = (start_hour - b_end) * 60
                # Backward gap: new booking ends before existing starts
                gap_before = (b_start - end_hour) * 60

                if 0 < gap_after < SAME_ROOM_GAP_MINUTES:
                    return False, f"Need {SAME_ROOM_GAP_MINUTES}min gap (only {gap_after:.0f}min after existing)"
                if 0 < gap_before < SAME_ROOM_GAP_MINUTES:
                    return False, f"Need {SAME_ROOM_GAP_MINUTES}min gap (only {gap_before:.0f}min before existing)"

        # Also check against existing reservations from agenda (if room is known)
        date_key = date.strftime('%Y-%m-%d')
        if date_key in self.reservation_ranges:
            for r_start, r_end, r_room in self.reservation_ranges[date_key]:
                if r_room and r_room == room:
                    # Same room - check gaps
                    gap_after = (start_hour - r_end) * 60
                    gap_before = (r_start - end_hour) * 60

                    if 0 < gap_after < SAME_ROOM_GAP_MINUTES:
                        return False, f"Need {SAME_ROOM_GAP_MINUTES}min gap from existing reservation (only {gap_after:.0f}min after)"
                    if 0 < gap_before < SAME_ROOM_GAP_MINUTES:
                        return False, f"Need {SAME_ROOM_GAP_MINUTES}min gap from existing reservation (only {gap_before:.0f}min before)"

        return True, ""

    def get_same_room_blocked_ranges(self, date, room):
        """Get time ranges blocked for a specific room on a date.

        Returns list of (blocked_start, blocked_end) tuples representing times that
        cannot be booked due to:
        1. Existing reservation in the same room
        2. 60-minute same-room gap rule before/after reservations
        """
        date_key = date.strftime('%Y-%m-%d')
        blocked = []
        gap_hours = SAME_ROOM_GAP_MINUTES / 60

        # Check existing reservations from agenda
        if date_key in self.reservation_ranges:
            for r_start, r_end, r_room in self.reservation_ranges[date_key]:
                if r_room and r_room == room:
                    # Block the reservation time itself
                    blocked.append((r_start, r_end))
                    # Block the gap period after this reservation ends
                    blocked.append((r_end, r_end + gap_hours))
                    # Block the gap period before this reservation starts
                    blocked.append((r_start - gap_hours, r_start))

        # Check bookings made during this session
        for b_room, b_date, b_start, b_end in self.bookings:
            if b_room == room and b_date.date() == date.date():
                # Block the booking time itself
                blocked.append((b_start, b_end))
                blocked.append((b_end, b_end + gap_hours))
                blocked.append((b_start - gap_hours, b_start))

        return blocked

    def bookings_today(self, date):
        """Count bookings on a specific date."""
        return sum(1 for _, d, _, _ in self.bookings if d.date() == date.date())


def get_available_slots(page):
    """Get available slots from the current calendar view."""
    return page.evaluate("""() => {
        const rows = document.querySelectorAll('.location-name-container .location-row');
        const locationDays = document.querySelectorAll('.location-day');

        function parseStylePct(style, prop) {
            const match = style.match(new RegExp(prop + ':\\\\s*calc\\\\(([\\\\d.]+)%'));
            return match ? parseFloat(match[1]) : null;
        }

        const pctPerHour = 6.25;
        let gridStartHour = 7.0;

        function pctToHour(pct) {
            return gridStartHour + (pct / pctPerHour);
        }

        function hourToPct(hour) {
            return (hour - gridStartHour) * pctPerHour;
        }

        const roomData = Array.from(rows).map((row, rowIdx) => {
            const rect = row.getBoundingClientRect();
            const roomName = row.textContent.trim();
            const midY = (rect.top + rect.bottom) / 2;

            let dayContainer = null;
            let dayRect = null;
            locationDays.forEach(day => {
                const dRect = day.getBoundingClientRect();
                if (Math.abs((dRect.top + dRect.bottom) / 2 - midY) < 30) {
                    dayContainer = day;
                    dayRect = dRect;
                }
            });

            if (!dayContainer) return { room: roomName, slots: [] };

            const events = dayContainer.querySelectorAll('.location-event');
            const blockedRanges = Array.from(events).map(ev => {
                const style = ev.getAttribute('style') || '';
                const leftPct = parseStylePct(style, 'left') || 0;
                const widthPct = parseStylePct(style, 'width') || 0;
                return {
                    startHour: pctToHour(leftPct),
                    endHour: pctToHour(leftPct + widthPct)
                };
            });

            const closedAreas = dayContainer.querySelectorAll('.location-closed');
            const closedRanges = Array.from(closedAreas).map(cl => {
                const style = cl.getAttribute('style') || '';
                const leftPct = parseStylePct(style, 'left') || 0;
                const widthPct = parseStylePct(style, 'width') || 0;
                return {
                    startHour: pctToHour(leftPct),
                    endHour: pctToHour(leftPct + widthPct)
                };
            });

            const allBlocked = [...blockedRanges, ...closedRanges]
                .sort((a, b) => a.startHour - b.startHour);

            const gaps = [];
            let lastEndHour = 7.2;

            let dayEndHour = 22.25;
            closedRanges.forEach(r => {
                if (r.startHour > 20) {
                    dayEndHour = r.startHour;
                }
            });

            allBlocked.forEach(range => {
                if (range.startHour > lastEndHour + 0.25) {
                    const startHour = Math.round(lastEndHour * 4) / 4;
                    const endHour = Math.round(range.startHour * 4) / 4;
                    if (endHour > startHour) {
                        const gapCenterPct = hourToPct((lastEndHour + range.startHour) / 2);
                        const clickX = dayRect.left + (gapCenterPct / 100) * dayRect.width;
                        gaps.push({ startHour, endHour, clickX, clickY: midY, roomName });
                    }
                }
                lastEndHour = Math.max(lastEndHour, range.endHour);
            });

            if (lastEndHour < dayEndHour - 0.25) {
                const startHour = Math.round(lastEndHour * 4) / 4;
                const endHour = Math.round(dayEndHour * 4) / 4;
                if (endHour > startHour) {
                    const gapCenterPct = hourToPct((lastEndHour + dayEndHour) / 2);
                    const clickX = dayRect.left + (gapCenterPct / 100) * dayRect.width;
                    gaps.push({ startHour, endHour, clickX, clickY: midY, roomName });
                }
            }

            return {
                room: roomName,
                slots: gaps
            };
        });

        return roomData.filter(r => r.slots.length > 0);
    }""")


def try_book_slot(page, slot, target_date, tracker, days_ahead):
    """Attempt to book a single slot. Returns True if successful."""
    room = slot['room']
    start_hour = slot['start_hour']
    slot_duration = (slot['end_hour'] - start_hour) * 60

    # Calculate maximum bookable duration based on horizon constraint
    # At the horizon edge, you can only book time that has "passed" the edge
    # E.g., at 9:30 AM, a 9:00 AM slot has 30 min past the horizon, so you can book 9:00-9:30
    horizon_days = ROOM_HORIZONS.get(room, DEFAULT_HORIZON)
    now = datetime.now()

    # Calculate when this slot became available
    if hasattr(target_date, 'date'):
        target_date_obj = target_date.date()
    else:
        target_date_obj = target_date
    slot_datetime = datetime.combine(target_date_obj, datetime.min.time())
    slot_datetime = slot_datetime.replace(
        hour=int(start_hour),
        minute=int((start_hour % 1) * 60)
    )
    available_from = slot_datetime - timedelta(days=horizon_days)

    # How much time has passed since the slot became available?
    time_since_available_mins = (now - available_from).total_seconds() / 60

    # The bookable duration is limited by:
    # 1. The slot's actual duration
    # 2. The max booking duration (2 hours)
    # 3. The time that has passed since horizon edge (for edge bookings)
    horizon_limited_duration = time_since_available_mins

    # Round down to nearest 15-minute interval for cleaner bookings
    horizon_limited_duration = (int(horizon_limited_duration) // 15) * 15

    # Take the minimum of all constraints
    booking_duration = min(slot_duration, MAX_BOOKING_HOURS * 60, horizon_limited_duration)

    # Check if we have at least 30 minutes to book
    if booking_duration < MIN_BOOKING_MINUTES:
        mins_until_bookable = MIN_BOOKING_MINUTES - horizon_limited_duration
        start_h = int(start_hour)
        start_m = int((start_hour % 1) * 60)
        book_start = f"{start_h:02d}:{start_m:02d}"
        print(f"  Skipping {room} {book_start}: Only {horizon_limited_duration:.0f}min past horizon (need {MIN_BOOKING_MINUTES}min, wait {mins_until_bookable:.0f}min)")
        # Extra debug for horizon issues
        print(f"    [DEBUG] Horizon calc: now={now.strftime('%H:%M:%S')}, slot={slot_datetime.strftime('%Y-%m-%d %H:%M')}, "
              f"available_from={available_from.strftime('%Y-%m-%d %H:%M')}, mins_since={time_since_available_mins:.1f}")
        return False

    end_hour = start_hour + booking_duration / 60

    # Format times
    start_h = int(start_hour)
    start_m = int((start_hour % 1) * 60)
    end_h = int(end_hour)
    end_m = int((end_hour % 1) * 60)
    book_start = f"{start_h:02d}:{start_m:02d}"
    book_end = f"{end_h:02d}:{end_m:02d}"

    # Check rules
    can_book, reason = tracker.can_book(room, target_date, start_hour, booking_duration)
    if not can_book:
        print(f"  Skipping {room} {book_start}: {reason}")
        return False

    # Note: We no longer need is_room_available_to_book() here since we already
    # calculated horizon availability above. But we keep it for safety.
    is_available, horizon_reason = is_room_available_to_book(room, target_date, start_hour)
    if not is_available:
        print(f"  Skipping {room} {book_start}: {horizon_reason}")
        return False

    # Log if this is a horizon-limited booking
    max_possible_duration = min(slot_duration, MAX_BOOKING_HOURS * 60)
    is_horizon_limited = booking_duration < max_possible_duration
    if is_horizon_limited:
        target_end_h = int(start_hour + max_possible_duration / 60)
        target_end_m = int((start_hour + max_possible_duration / 60) % 1 * 60)
        print(f"\n{'='*60}")
        print(f"Trying: {room} at {book_start}-{book_end} (HORIZON LIMITED)")
        print(f"  Will extend later: target {book_start}-{target_end_h:02d}:{target_end_m:02d}")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print(f"Trying: {room} at {book_start}-{book_end}")
        print(f"{'='*60}")

    # Step 1: Click on empty slot
    # Find the room row and scroll it into view, then get fresh coordinates
    room_name = slot['room']
    # Note: end_hour was already calculated above with horizon limits applied

    # Calculate click percentage for the target time
    grid_start_hour = 7.0
    pct_per_hour = 6.25
    target_center = (start_hour + end_hour) / 2
    click_pct = (target_center - grid_start_hour) * pct_per_hour
    print(f"  [DEBUG] Click calculation: slot={start_hour:.2f}-{end_hour:.2f}, center={target_center:.2f}, click_pct={click_pct:.2f}%")

    # Find the room row and get fresh click coordinates
    coords = page.evaluate(f"""() => {{
        const rows = document.querySelectorAll('.location-name-container .location-row');
        const locationDays = document.querySelectorAll('.location-day');

        // Find the row for this room
        let targetRow = null;
        for (const row of rows) {{
            if (row.textContent.trim() === '{room_name}') {{
                targetRow = row;
                break;
            }}
        }}
        if (!targetRow) return null;

        // Scroll row into view and wait for layout to settle
        targetRow.scrollIntoView({{ behavior: 'instant', block: 'center' }});

        // Force a reflow to ensure scroll completes
        void targetRow.offsetHeight;

        // Find the matching location-day for this row
        const rowRect = targetRow.getBoundingClientRect();
        const rowMidY = (rowRect.top + rowRect.bottom) / 2;

        let dayRect = null;
        for (const day of locationDays) {{
            const dRect = day.getBoundingClientRect();
            const dayMidY = (dRect.top + dRect.bottom) / 2;
            if (Math.abs(dayMidY - rowMidY) < 30) {{
                dayRect = dRect;
                break;
            }}
        }}
        if (!dayRect) return null;

        const clickX = dayRect.left + ({click_pct} / 100) * dayRect.width;
        const clickY = (rowRect.top + rowRect.bottom) / 2;

        return {{
            x: clickX,
            y: clickY,
            dayLeft: dayRect.left,
            dayWidth: dayRect.width,
            dayRight: dayRect.right
        }};
    }}""")

    if not coords:
        print(f"  [DEBUG] Could not find room '{room_name}' in grid")
        print(f"  Could not find room '{room_name}' in grid - skipping")
        return False

    page.wait_for_timeout(300)  # Brief wait after scroll

    # Log viewport info
    viewport = page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight, scrollY: window.scrollY })")
    print(f"  [DEBUG] Viewport: {viewport['width']}x{viewport['height']}, scrollY={viewport['scrollY']:.0f}")
    print(f"  [DEBUG] Day grid: left={coords.get('dayLeft', 0):.0f}, width={coords.get('dayWidth', 0):.0f}, right={coords.get('dayRight', 0):.0f}")
    print(f"  [DEBUG] Click target: x={coords['x']:.0f}, y={coords['y']:.0f} (target time: {start_hour:.2f}-{end_hour:.2f})")

    # Check if click is within viewport
    if coords['y'] < 0 or coords['y'] > viewport['height']:
        print(f"  [DEBUG] WARNING: Click Y={coords['y']:.0f} is outside viewport (0-{viewport['height']})")

    # Try clicking at different positions if needed - start with center, then try offset positions
    click_positions = [
        (coords['x'], coords['y'], "center"),
        (coords['x'] + 15, coords['y'] + 5, "offset right-bottom"),
        (coords['x'] - 15, coords['y'] - 5, "offset left-top"),
    ]

    popup_found = False
    current_url = page.url

    for click_x, click_y, pos_desc in click_positions:
        print(f"  Clicking at ({click_x:.0f}, {click_y:.0f}) for {room_name} ({pos_desc})...")
        page.mouse.click(click_x, click_y)

        # Wait for popup or navigation with retry
        for wait_attempt in range(2):
            page.wait_for_timeout(1000)  # Wait 1s between checks

            # Check if clicking directly navigated to booking form (URL contains /event?eventId=0)
            current_url = page.url
            if '/event?eventId=0' in current_url:
                print(f"  [DEBUG] Click navigated directly to booking form")
                popup_found = True
                break

            # Check if popup with booking option appeared
            student_btn = page.locator("mat-list-item:has-text('Student booking provisional')").first
            if student_btn.count() > 0 and student_btn.is_visible():
                popup_found = True
                break

            # Check for overlay WITH "Student booking provisional" text
            overlays = page.locator(".cdk-overlay-pane, .mat-menu-panel, mat-bottom-sheet-container").all()
            for overlay in overlays:
                if overlay.is_visible():
                    text = overlay.text_content() or ""
                    if 'Student booking provisional' in text:
                        popup_found = True
                        break
            if popup_found:
                break

        if popup_found:
            break

        # If we got a tooltip but not the booking menu, dismiss it and try another position
        overlays = page.locator(".cdk-overlay-pane, .mat-menu-panel, mat-bottom-sheet-container").all()
        visible_overlays = [o for o in overlays if o.is_visible()]
        if visible_overlays:
            # Got a tooltip, not the booking menu - dismiss and try again
            tooltip_text = visible_overlays[0].text_content() or ""
            print(f"  [DEBUG] Got tooltip instead of menu: {tooltip_text[:80]}...")
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        else:
            print(f"  [DEBUG] No popup at {pos_desc} position")

    if '/event?eventId=0' in current_url:
        print(f"  [DEBUG] Click navigated directly to booking form")
        # Already on booking form, skip popup handling and go to Step 3
        pass  # Continue to Step 3 below
    elif not popup_found:
        # Check if we accidentally clicked on an existing reservation
        # The unique indicator is "Events where I participate" or "Choose which events to display"
        events_participate = page.locator("text=Events where I participate").first
        choose_events = page.locator("text=Choose which events to display").first

        ep_visible = events_participate.count() > 0 and events_participate.is_visible()
        ce_visible = choose_events.count() > 0 and choose_events.is_visible()

        if ep_visible or ce_visible:
            print(f"  [DEBUG] Clicked on existing reservation (events_participate={ep_visible}, choose_events={ce_visible})")
            print("  Clicked on existing reservation - clicking back button...")

            # Click the back button with aria-label="Back to previous page"
            back_btn = page.locator("button[aria-label='Back to previous page']").first
            if back_btn.count() > 0 and back_btn.is_visible():
                back_btn.click()
                page.wait_for_timeout(2000)
                print("    Clicked back button")
            else:
                # Fallback: use browser back
                page.go_back()
                page.wait_for_timeout(2000)
                print("    Used browser back")
            return False

        # No booking popup found after trying all positions
        print("  No booking popup found after trying multiple click positions - skipping")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        return False
    else:
        # Step 2: Click "Student booking provisional" - we know the popup is visible
        print(f"  [DEBUG] Looking for 'Student booking provisional' button...")
        student_btn = page.locator("mat-list-item:has-text('Student booking provisional')").first
        if student_btn.count() > 0 and student_btn.is_visible():
            print("  Found 'Student booking provisional' button")
            student_btn.click()
            page.wait_for_timeout(3000)
        else:
            # Try alternative locators
            alt_btn = page.locator("text=Student booking provisional").first
            if alt_btn.count() > 0 and alt_btn.is_visible():
                print("  Found alt 'Student booking provisional' text")
                alt_btn.click()
                page.wait_for_timeout(3000)
            else:
                # Check overlays for the button
                overlays = page.locator(".cdk-overlay-pane, .mat-menu-panel, mat-bottom-sheet-container").all()
                found_in_overlay = False
                for overlay in overlays:
                    if overlay.is_visible():
                        text = overlay.text_content() or ""
                        if 'Student booking provisional' in text:
                            overlay_btn = overlay.locator("text=Student booking provisional").first
                            if overlay_btn.count() > 0:
                                print("  Found 'Student booking provisional' in overlay, clicking...")
                                overlay_btn.click()
                                page.wait_for_timeout(3000)
                                found_in_overlay = True
                                break

                if not found_in_overlay:
                    print("  [DEBUG] Could not find 'Student booking provisional' button despite popup_found=True")
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                    return False

    # Step 3: Set times
    print(f"  [DEBUG] Setting times: {book_start} to {book_end}")
    start_input = page.locator("#startDate")
    end_input = page.locator("#endDate")

    if start_input.count() > 0 and end_input.count() > 0:
        print(f"  [DEBUG] Found time inputs, filling start time...")
        start_input.click()
        start_input.fill(book_start)
        page.wait_for_timeout(300)

        print(f"  [DEBUG] Filling end time...")
        end_input.click()
        end_input.fill(book_end)
        page.wait_for_timeout(300)

        # Read back the values to verify
        actual_start = start_input.input_value()
        actual_end = end_input.input_value()
        print(f"  [DEBUG] Verified times: start={actual_start}, end={actual_end}")

        # Dismiss dropdown
        save_btn = page.locator("button:has-text('Save')").first
        if save_btn.count() > 0:
            box = save_btn.bounding_box()
            if box:
                page.mouse.click(box['x'] + 10, box['y'] - 20)
        page.wait_for_timeout(500)
    else:
        print(f"  [DEBUG] WARNING: Time inputs not found! start={start_input.count()}, end={end_input.count()}")

    # Step 4: Check for errors - look for ANY conflict indicator
    # First, search for any reservation time patterns on the page (your existing bookings)
    print(f"  [DEBUG] Checking for errors/conflicts on booking form...")
    all_reservation_times = page.locator("text=/\\d{2}:\\d{2}\\s*-\\s*\\d{2}:\\d{2}/").all()
    print(f"  [DEBUG] Found {len(all_reservation_times)} time patterns on page")
    reservations_found = 0
    for res in all_reservation_times:
        try:
            text = res.text_content() or ""
            # Look for patterns like "09:30 - 11:00 Reservation" or similar
            if "Reservation" in text or "Student" in text:
                match = re.search(r'(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})', text)
                if match:
                    c_start = int(match.group(1)) + int(match.group(2)) / 60
                    c_end = int(match.group(3)) + int(match.group(4)) / 60
                    # Add to conflicts if not already known
                    if not tracker.overlaps_conflict(target_date, c_start, c_end):
                        tracker.add_conflict(target_date, c_start, c_end)
                        print(f"  Found existing reservation: {match.group(1)}:{match.group(2)}-{match.group(3)}:{match.group(4)}")
                        reservations_found += 1
        except:
            pass
    if reservations_found > 0:
        print(f"  [DEBUG] Added {reservations_found} new conflict(s) from booking form")

    error_msg = page.locator("text=You are not allowed").first
    if error_msg.count() > 0 and error_msg.is_visible():
        # Get the full error text for debugging
        full_error = error_msg.text_content() or "Unknown error"
        print(f"  Error: Not allowed to book")
        print(f"  [DEBUG] Error message: {full_error[:100]}")

        # Extract conflict time
        conflict_text = page.locator("text=/\\d{2}:\\d{2}\\s*-\\s*\\d{2}:\\d{2}.*Reservation/").first
        if conflict_text.count() > 0:
            text = conflict_text.text_content() or ""
            print(f"  [DEBUG] Found conflict: {text[:80]}")
            match = re.search(r'(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})', text)
            if match:
                c_start = int(match.group(1)) + int(match.group(2)) / 60
                c_end = int(match.group(3)) + int(match.group(4)) / 60
                tracker.add_conflict(target_date, c_start, c_end)

        go_back(page, days_ahead)
        return False

    # Check for conflicts
    conflict_msg = page.locator("text=conflicting events").first
    resolve_msg = page.locator("text=resolve the conflicts").first
    clash_msg = page.locator("text=booking clashes").first

    has_conflict = (conflict_msg.count() > 0 and conflict_msg.is_visible()) or \
                   (resolve_msg.count() > 0 and resolve_msg.is_visible()) or \
                   (clash_msg.count() > 0 and clash_msg.is_visible())

    if has_conflict:
        print("  Error: Conflicts with existing booking")

        # Extract conflict time from the booking list
        conflict_text = page.locator("text=/\\d{2}:\\d{2}\\s*-\\s*\\d{2}:\\d{2}.*Reservation/").first
        if conflict_text.count() > 0:
            text = conflict_text.text_content() or ""
            match = re.search(r'(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})', text)
            if match:
                c_start = int(match.group(1)) + int(match.group(2)) / 60
                c_end = int(match.group(3)) + int(match.group(4)) / 60
                tracker.add_conflict(target_date, c_start, c_end)

        go_back(page, days_ahead)
        return False

    # Re-check if our slot now overlaps with conflicts we just discovered
    if tracker.overlaps_conflict(target_date, start_hour, end_hour):
        print(f"  Skipping - overlaps with newly discovered conflict")
        go_back(page, days_ahead)
        return False

    # Final conflict check before save - look for yellow/red warning areas
    warning_elements = page.locator(".warning, .error, [class*='conflict'], [class*='clash']").all()
    for warn in warning_elements:
        if warn.is_visible():
            text = warn.text_content() or ""
            if "conflict" in text.lower() or "clash" in text.lower() or "not allowed" in text.lower():
                print(f"  Warning found: {text[:50]}...")
                go_back(page, days_ahead)
                return False

    # Step 5: Click Save
    print(f"  [DEBUG] Looking for Save button...")
    save_btn = page.locator("button:has-text('Save')").first
    if save_btn.count() > 0 and save_btn.is_visible():
        # Check if enabled - look for disabled icons (remove, block, close, cancel, etc.)
        save_icon = save_btn.locator("mat-icon").first
        if save_icon.count() > 0:
            icon_text = save_icon.text_content() or ""
            print(f"  [DEBUG] Save button icon: '{icon_text}'")
            # Check for any "disabled" style icon (remove_circle, block, close, cancel, etc.)
            disabled_icons = ["remove", "block", "close", "cancel", "error", "warning"]
            if any(disabled in icon_text.lower() for disabled in disabled_icons):
                print(f"  Save disabled (icon: {icon_text})")

                # Try to capture the actual error message from the booking form
                error_reason = "Unknown reason"

                # Look for common Asimut error messages
                error_selectors = [
                    "text=You are not allowed",
                    "text=not allowed to book",
                    "text=maximum number",
                    "text=quota",
                    "text=peak hours",
                    "text=already booked",
                    "text=conflict",
                    "text=overlaps",
                    ".mat-error",
                    ".error-message",
                    "[class*='error']",
                    "[class*='warning']",
                    "mat-hint.mat-error",
                ]

                for selector in error_selectors:
                    try:
                        error_el = page.locator(selector).first
                        if error_el.count() > 0 and error_el.is_visible():
                            error_text = error_el.text_content() or ""
                            if error_text.strip() and len(error_text) > 5:
                                error_reason = error_text.strip()[:100]
                                break
                    except:
                        pass

                # Also check the page for any visible text containing error keywords
                if error_reason == "Unknown reason":
                    try:
                        page_text = page.locator("body").text_content() or ""
                        # Look for quota-related messages
                        if "maximum" in page_text.lower() and ("booking" in page_text.lower() or "hour" in page_text.lower()):
                            # Extract the relevant sentence
                            for line in page_text.split('\n'):
                                if "maximum" in line.lower() or "quota" in line.lower() or "limit" in line.lower():
                                    error_reason = line.strip()[:100]
                                    break
                    except:
                        pass

                print(f"  [DEBUG] Rejection reason: {error_reason}")
                go_back(page, days_ahead)
                return False

        # Get button state before clicking
        btn_box = save_btn.bounding_box()
        print(f"  [DEBUG] Save button at: x={btn_box['x']:.0f}, y={btn_box['y']:.0f}" if btn_box else "  [DEBUG] Save button box: None")

        print("  Clicking Save...")
        save_btn.click()
        page.wait_for_timeout(3000)

        # Check for post-save errors first
        post_conflict = page.locator("text=conflicting events").first
        post_not_allowed = page.locator("text=not allowed").first

        if post_conflict.count() > 0 and post_conflict.is_visible():
            print("  Save failed - conflict after save")
            print(f"  [DEBUG] Conflict text: {post_conflict.text_content()[:80] if post_conflict.text_content() else 'N/A'}...")
            go_back(page, days_ahead)
            return False

        if post_not_allowed.count() > 0 and post_not_allowed.is_visible():
            print("  Save failed - not allowed after save")
            print(f"  [DEBUG] Not allowed text: {post_not_allowed.text_content()[:80] if post_not_allowed.text_content() else 'N/A'}...")
            go_back(page, days_ahead)
            return False

        # Check success - booking form should be gone
        booking_form = page.locator("#startDate")
        current_url = page.url
        print(f"  [DEBUG] After save - form visible: {booking_form.is_visible() if booking_form.count() > 0 else 'N/A'}, URL: {current_url[:50]}...")

        if booking_form.count() == 0 or not booking_form.is_visible():
            # Success!
            tracker.add_booking(room, target_date, start_hour, end_hour)
            print(f"  SUCCESS: Booked {room} {book_start}-{book_end}")

            # Check if we booked less than the full slot due to horizon constraints
            # If so, mark for later extension
            original_slot_end = slot['end_hour']
            max_possible_end = min(original_slot_end, start_hour + MAX_BOOKING_HOURS)
            if end_hour < max_possible_end:
                # Check if this is because we're at the horizon edge
                horizon_days = ROOM_HORIZONS.get(room, DEFAULT_HORIZON)
                slot_datetime = datetime.combine(target_date, datetime.min.time())
                slot_datetime = slot_datetime.replace(
                    hour=int(start_hour),
                    minute=int((start_hour % 1) * 60)
                )
                available_from = slot_datetime - timedelta(days=horizon_days)
                now = datetime.now()
                time_since_available = (now - available_from).total_seconds() / 60

                # If we just became available (within last 60 mins), mark for extension
                if time_since_available < 60:
                    save_extendable_booking(room, target_date, start_hour, end_hour, max_possible_end)

            return True
        else:
            # Check if there's an actual error or just delay
            print(f"  [DEBUG] Form still visible, waiting 1 more second...")
            page.wait_for_timeout(1000)
            booking_form = page.locator("#startDate")
            if booking_form.count() == 0 or not booking_form.is_visible():
                # Success after extra wait
                tracker.add_booking(room, target_date, start_hour, end_hour)
                print(f"  SUCCESS: Booked {room} {book_start}-{book_end}")

                # Check if we booked less than the full slot due to horizon constraints
                original_slot_end = slot['end_hour']
                max_possible_end = min(original_slot_end, start_hour + MAX_BOOKING_HOURS)
                if end_hour < max_possible_end:
                    horizon_days = ROOM_HORIZONS.get(room, DEFAULT_HORIZON)
                    slot_datetime = datetime.combine(target_date, datetime.min.time())
                    slot_datetime = slot_datetime.replace(
                        hour=int(start_hour),
                        minute=int((start_hour % 1) * 60)
                    )
                    available_from = slot_datetime - timedelta(days=horizon_days)
                    now = datetime.now()
                    time_since_available = (now - available_from).total_seconds() / 60

                    if time_since_available < 60:
                        save_extendable_booking(room, target_date, start_hour, end_hour, max_possible_end)

                return True

            # Log what's visible on the page
            page_text = page.evaluate("() => document.body.innerText.substring(0, 500)")
            print(f"  [DEBUG] Page content preview: {page_text[:200]}...")
            print("  Save may have failed - still on form")
            go_back(page, days_ahead)
            return False
    else:
        print(f"  [DEBUG] Save button not found: count={save_btn.count()}, visible={save_btn.is_visible() if save_btn.count() > 0 else 'N/A'}")
        print("  Save button not found")
        go_back(page, days_ahead)
        return False


def find_horizon_snipe_candidate(available_data, target_date, tracker, time_prefs):
    """
    Find a slot that's about to become bookable (currently <30min past horizon).

    Returns: (slot_info, seconds_until_bookable) or (None, None) if no candidate found.
    """
    now = datetime.now()

    if hasattr(target_date, 'date'):
        target_date_obj = target_date.date()
    else:
        target_date_obj = target_date

    best_candidate = None
    best_wait_seconds = float('inf')

    for room_data in available_data:
        room_name = room_data["room"]
        if room_name not in PRIORITY_ROOMS:
            continue

        horizon_days = ROOM_HORIZONS.get(room_name, DEFAULT_HORIZON)
        room_priority = PRIORITY_ROOMS.index(room_name)

        for slot in room_data["slots"]:
            start_hour = slot['startHour']
            slot_duration = (slot['endHour'] - slot['startHour']) * 60

            if slot_duration < MIN_BOOKING_MINUTES:
                continue

            # Check time preferences if strict mode is on
            if time_prefs["enabled"] and time_prefs["strict_mode"]:
                if not is_preferred_time(start_hour, time_prefs):
                    continue

            # Calculate when this slot becomes bookable (30 min past horizon)
            slot_datetime = datetime.combine(target_date_obj, datetime.min.time())
            slot_datetime = slot_datetime.replace(
                hour=int(start_hour),
                minute=int((start_hour % 1) * 60)
            )
            available_from = slot_datetime - timedelta(days=horizon_days)
            bookable_from = available_from + timedelta(minutes=MIN_BOOKING_MINUTES)

            # How long until this slot is bookable?
            seconds_until_bookable = (bookable_from - now).total_seconds()

            # We want slots that will become bookable soon (within 3 minutes)
            # but aren't bookable yet
            if 0 < seconds_until_bookable <= 180:  # 0 to 3 minutes away
                # Check if we can actually book this (quota, conflicts, etc.)
                can_book, reason = tracker.can_book(room_name, target_date, start_hour, MIN_BOOKING_MINUTES)
                if not can_book:
                    continue

                # Prefer slots that become available soonest, then by room priority
                if seconds_until_bookable < best_wait_seconds or \
                   (seconds_until_bookable == best_wait_seconds and room_priority < (best_candidate['room_priority'] if best_candidate else 999)):
                    best_candidate = {
                        "room": room_name,
                        "start_hour": start_hour,
                        "end_hour": slot['endHour'],
                        "duration": slot_duration,
                        "room_priority": room_priority,
                        "click_x": slot["clickX"],
                        "click_y": slot["clickY"],
                        "bookable_from": bookable_from,
                        "horizon_days": horizon_days
                    }
                    best_wait_seconds = seconds_until_bookable

    if best_candidate:
        return best_candidate, best_wait_seconds
    return None, None


def try_horizon_snipe(page, slot, target_date, tracker, days_ahead):
    """
    Prepare a booking form for a slot that's about to become bookable,
    then click Save at the exact moment it becomes available.

    This "snipes" the slot by having everything ready before other users.

    Returns: True if successful, False otherwise.
    """
    room = slot['room']
    start_hour = slot['start_hour']
    bookable_from = slot['bookable_from']
    horizon_days = slot['horizon_days']

    # Calculate the 30-minute booking window
    end_hour = start_hour + MIN_BOOKING_MINUTES / 60

    # Format times
    start_h = int(start_hour)
    start_m = int((start_hour % 1) * 60)
    end_h = int(end_hour)
    end_m = int((end_hour % 1) * 60)
    book_start = f"{start_h:02d}:{start_m:02d}"
    book_end = f"{end_h:02d}:{end_m:02d}"

    now = datetime.now()
    seconds_to_wait = (bookable_from - now).total_seconds()

    print(f"\n{'='*60}")
    print(f"HORIZON SNIPE: {room} at {book_start}-{book_end}")
    print(f"{'='*60}")
    print(f"  Slot becomes bookable at: {bookable_from.strftime('%H:%M:%S')}")
    print(f"  Current time: {now.strftime('%H:%M:%S')}")
    print(f"  Seconds until bookable: {seconds_to_wait:.1f}")
    print(f"  Strategy: Open form now, wait, click Save at exact moment")

    # Step 1: Click on the slot to open booking form
    room_name = slot['room']
    grid_start_hour = 7.0
    pct_per_hour = 6.25
    target_center = (start_hour + end_hour) / 2
    click_pct = (target_center - grid_start_hour) * pct_per_hour

    print(f"\n  [SNIPE] Step 1: Opening booking form...")

    # Find the room row and get click coordinates
    coords = page.evaluate(f"""() => {{
        const rows = document.querySelectorAll('.location-name-container .location-row');
        const locationDays = document.querySelectorAll('.location-day');

        let targetRow = null;
        for (const row of rows) {{
            if (row.textContent.trim() === '{room_name}') {{
                targetRow = row;
                break;
            }}
        }}
        if (!targetRow) return null;

        targetRow.scrollIntoView({{ behavior: 'instant', block: 'center' }});
        void targetRow.offsetHeight;

        const rowRect = targetRow.getBoundingClientRect();
        const rowMidY = (rowRect.top + rowRect.bottom) / 2;

        let dayRect = null;
        for (const day of locationDays) {{
            const dRect = day.getBoundingClientRect();
            const dayMidY = (dRect.top + dRect.bottom) / 2;
            if (Math.abs(dayMidY - rowMidY) < 30) {{
                dayRect = dRect;
                break;
            }}
        }}
        if (!dayRect) return null;

        const clickX = dayRect.left + ({click_pct} / 100) * dayRect.width;
        const clickY = (rowRect.top + rowRect.bottom) / 2;

        return {{ x: clickX, y: clickY }};
    }}""")

    if not coords:
        print(f"  [SNIPE] Could not find room '{room_name}' - aborting snipe")
        return False

    page.wait_for_timeout(200)

    # Click to open booking form
    print(f"  [SNIPE] Clicking at ({coords['x']:.0f}, {coords['y']:.0f})...")
    page.mouse.click(coords['x'], coords['y'])
    page.wait_for_timeout(500)

    # Look for "Student booking provisional" button
    student_booking_btn = page.locator("mat-list-item").filter(has_text="Student booking provisional")
    if student_booking_btn.count() > 0 and student_booking_btn.is_visible():
        print(f"  [SNIPE] Found booking type button, clicking...")
        student_booking_btn.click()
        page.wait_for_timeout(800)
    else:
        print(f"  [SNIPE] No booking type button found, checking if form opened...")

    # Step 2: Fill in the times
    print(f"  [SNIPE] Step 2: Pre-filling times ({book_start} to {book_end})...")

    time_inputs = page.locator("input[type='time'], input[formcontrolname*='time'], input[data-cy*='time']")
    if time_inputs.count() >= 2:
        # Fill start time
        start_input = time_inputs.nth(0)
        start_input.click()
        start_input.fill(book_start)
        page.wait_for_timeout(200)

        # Fill end time
        end_input = time_inputs.nth(1)
        end_input.click()
        end_input.fill(book_end)
        page.wait_for_timeout(200)

        # Verify times
        actual_start = start_input.input_value()
        actual_end = end_input.input_value()
        print(f"  [SNIPE] Times set: {actual_start} to {actual_end}")
    else:
        print(f"  [SNIPE] Could not find time inputs (found {time_inputs.count()}) - aborting")
        go_back(page, days_ahead)
        return False

    # Step 3: Locate Save button
    print(f"  [SNIPE] Step 3: Locating Save button...")
    save_btn = page.locator("button").filter(has_text="Save").first

    if save_btn.count() == 0 or not save_btn.is_visible():
        print(f"  [SNIPE] Save button not found - aborting")
        go_back(page, days_ahead)
        return False

    save_box = save_btn.bounding_box()
    if not save_box:
        print(f"  [SNIPE] Could not get Save button position - aborting")
        go_back(page, days_ahead)
        return False

    save_x = save_box['x'] + save_box['width'] / 2
    save_y = save_box['y'] + save_box['height'] / 2
    print(f"  [SNIPE] Save button located at ({save_x:.0f}, {save_y:.0f})")

    # Step 4: Wait until the exact moment, then click Save
    print(f"\n  [SNIPE] Step 4: Waiting for bookable moment...")

    # Update timing
    now = datetime.now()
    seconds_to_wait = (bookable_from - now).total_seconds()

    if seconds_to_wait > 0:
        print(f"  [SNIPE] Waiting {seconds_to_wait:.1f} seconds...")

        # Countdown in the final seconds
        while True:
            now = datetime.now()
            remaining = (bookable_from - now).total_seconds()

            if remaining <= 0:
                break
            elif remaining <= 10:
                print(f"  [SNIPE] T-{remaining:.1f}s...")
                time.sleep(0.1)  # Check every 100ms in final 10 seconds
            elif remaining <= 30:
                print(f"  [SNIPE] T-{remaining:.0f}s...")
                time.sleep(1)
            else:
                time.sleep(1)

    # Wait 1 second after bookable time to ensure system is ready
    print(f"  [SNIPE] Adding 1 second safety buffer...")
    time.sleep(1)

    # Click Save!
    click_time = datetime.now()
    print(f"\n  >>> CLICKING SAVE NOW: {click_time.strftime('%H:%M:%S.%f')[:-3]} <<<")

    page.mouse.click(save_x, save_y)

    # Wait for result
    page.wait_for_timeout(2000)

    # Check if booking succeeded
    current_url = page.url
    booking_form = page.locator("app-arrangement-upsert, .booking-form, [class*='upsert']")

    if "eventId" in current_url or (booking_form.count() == 0 or not booking_form.is_visible()):
        # Success!
        result_time = datetime.now()
        print(f"  [SNIPE] SUCCESS! Booked at {result_time.strftime('%H:%M:%S.%f')[:-3]}")
        print(f"  [SNIPE] Latency from bookable time: {(result_time - bookable_from).total_seconds()*1000:.0f}ms")

        # Add to tracker
        tracker.add_booking(room, target_date, start_hour, end_hour)
        print(f"  SUCCESS: Booked {room} {book_start}-{book_end}")

        # Save as extendable (we booked minimum 30 min, can extend later)
        max_possible_duration = min(slot['duration'], MAX_BOOKING_HOURS * 60)
        if max_possible_duration > MIN_BOOKING_MINUTES:
            target_end_hour = start_hour + max_possible_duration / 60
            save_extendable_booking(room, target_date, start_hour, end_hour, target_end_hour)
            target_end_h = int(target_end_hour)
            target_end_m = int((target_end_hour % 1) * 60)
            print(f"  [EXTEND] Marked for extension: target {book_start}-{target_end_h:02d}:{target_end_m:02d}")

        return True
    else:
        # Check for error messages
        page_text = page.locator("body").inner_text()
        if "conflict" in page_text.lower() or "error" in page_text.lower():
            print(f"  [SNIPE] FAILED - Conflict or error detected")
        else:
            print(f"  [SNIPE] FAILED - Still on booking form")

        go_back(page, days_ahead)
        return False


def go_back(page, days_ahead=None):
    """Navigate back to calendar by clicking the back button."""
    print(f"  [DEBUG] go_back() called")
    try:
        # First try Escape to close any dialogs/modals
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

        # Check if we're still on booking form
        booking_form = page.locator("#startDate")
        form_visible = booking_form.count() > 0 and booking_form.is_visible()
        print(f"  [DEBUG] Booking form visible: {form_visible}")

        if form_visible:
            # Try the back button with aria-label
            back_btn = page.locator("button[aria-label='Back to previous page']").first
            if back_btn.count() > 0 and back_btn.is_visible():
                print(f"  [DEBUG] Clicking back button...")
                back_btn.click()
                page.wait_for_timeout(2000)
            else:
                # Try clicking any close/cancel button
                close_btn = page.locator("button:has-text('Close'), button:has-text('Cancel')").first
                if close_btn.count() > 0 and close_btn.is_visible():
                    print(f"  [DEBUG] Clicking close/cancel button...")
                    close_btn.click()
                    page.wait_for_timeout(2000)
                else:
                    # Fallback: use browser back
                    print(f"  [DEBUG] Using browser back navigation...")
                    page.go_back()
                    page.wait_for_timeout(2000)

        # Verify we're back on calendar (wait for location-day elements)
        page.wait_for_selector(".location-day", timeout=5000)
        print(f"  [DEBUG] Back on calendar (location-day found)")

    except Exception as e:
        print(f"  [DEBUG] go_back exception: {e}")
        print(f"  Warning: go_back issue ({e}), trying Escape + browser back...")
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            page.go_back()
            page.wait_for_timeout(2000)
        except:
            pass


def scan_agenda(page, tracker, today):
    """Scan the My Agenda page to find all existing events and reservations for the next 7 days."""
    print("\n" + "="*60)
    print("SCANNING MY AGENDA FOR EXISTING EVENTS")
    print("="*60)

    # Navigate directly to the agenda page
    print("  [DEBUG] Navigating to agenda page...")
    page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
    page.wait_for_timeout(3000)
    print(f"  [DEBUG] Agenda page loaded, URL: {page.url[:50]}...")

    # Calculate the target date (7 days from now)
    target_date = today + timedelta(days=7)
    target_date_str = target_date.strftime("%d %B %Y").lstrip("0")  # e.g., "7 February 2026"
    print(f"  Scrolling until we reach {target_date_str}...")

    # Keep scrolling until we see the target date or reach the end
    max_scrolls = 50  # Safety limit
    scroll_count = 0
    last_height = 0
    stalled_count = 0  # Track consecutive scrolls with no height change
    max_stalled = 3  # Allow a few stalled scrolls before giving up (lazy loading)

    while scroll_count < max_scrolls:
        # Check if target date is visible on page
        page_text = page.evaluate("() => document.body.innerText")
        if target_date_str in page_text:
            print(f"  [DEBUG] Found target date '{target_date_str}' after {scroll_count} scrolls")
            break

        # Also check alternative date format (e.g., "7 February 2026" vs "07 February 2026")
        alt_date_str = target_date.strftime("%d %B %Y")  # With leading zero
        if alt_date_str in page_text:
            print(f"  [DEBUG] Found target date '{alt_date_str}' after {scroll_count} scrolls")
            break

        # Scroll down - use scrollTo for more reliable scrolling
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1000)  # Wait longer for lazy loading

        # Check if we've reached the bottom (no new content)
        new_height = page.evaluate("() => document.body.scrollHeight")
        if new_height == last_height:
            stalled_count += 1
            if stalled_count >= max_stalled:
                print(f"  [DEBUG] Reached end of agenda page after {scroll_count} scrolls (height: {new_height})")
                break
            # Try scrolling again - content might still be loading
            page.wait_for_timeout(500)
        else:
            stalled_count = 0  # Reset if we got new content
        last_height = new_height
        scroll_count += 1
        if scroll_count % 5 == 0:
            print(f"  [DEBUG] Still scrolling... ({scroll_count} scrolls, height: {new_height})")

    # Scroll back to top to ensure we capture everything
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(1000)

    # Now scroll through the entire page once more to ensure all content is in DOM
    # Use a minimum of 10 scrolls to ensure we cover at least a week
    min_scrolls = max(scroll_count + 5, 10)
    print(f"  Final pass to capture all events ({min_scrolls} scrolls)...")
    for i in range(min_scrolls):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(400)
        if i % 5 == 4:
            # Extra wait every 5 scrolls for lazy loading
            page.wait_for_timeout(300)

    page.wait_for_timeout(1000)

    # Extract events using JavaScript to get structured data
    # This version checks for cancelled events and also captures event titles to identify reservations
    events_data = page.evaluate("""() => {
        const events = [];
        const today = new Date();
        today.setHours(0, 0, 0, 0);

        const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                          'July', 'August', 'September', 'October', 'November', 'December'];

        // Helper function to check if an element or its parents indicate a cancelled event
        function isCancelled(element) {
            let el = element;
            while (el && el !== document.body) {
                const style = window.getComputedStyle(el);
                // Check for strikethrough text
                if (style.textDecoration.includes('line-through') ||
                    style.textDecorationLine.includes('line-through')) {
                    return true;
                }
                // Check for cancelled class names
                const className = el.className || '';
                if (className.includes('cancelled') || className.includes('canceled') ||
                    className.includes('deleted') || className.includes('removed')) {
                    return true;
                }
                // Check for red background/color that might indicate cancellation
                const bgColor = style.backgroundColor;
                // Parse RGB values - look for predominantly red colors
                const redBgMatch = bgColor.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/);
                if (redBgMatch) {
                    const r = parseInt(redBgMatch[1]);
                    const g = parseInt(redBgMatch[2]);
                    const b = parseInt(redBgMatch[3]);
                    // If red is dominant and significantly higher than green/blue
                    if (r > 180 && r > g * 1.5 && r > b * 1.5) {
                        return true;
                    }
                }
                el = el.parentElement;
            }
            return false;
        }

        // Helper to get the event title from the event container
        // Asimut uses app-as-event components with specific data-cy attributes
        function getEventTitle(element) {
            // Find the closest app-as-event container (the event card)
            let container = element;
            for (let i = 0; i < 10 && container; i++) {
                // Check if we found the event container
                if (container.tagName === 'APP-AS-EVENT' ||
                    container.classList.contains('as-event-panel') ||
                    container.classList.contains('event-details-expanded')) {

                    // Look for the event-display-name element
                    const displayName = container.querySelector('[data-cy="event-display-name"]');
                    if (displayName) {
                        const nameText = (displayName.textContent || '').trim();
                        if (nameText === 'Reservation' || nameText.includes('Reservation')) {
                            return 'Reservation';
                        }
                    }

                    // Also check for location-link with room pattern (B0.xx or B1.xx)
                    const locationLink = container.querySelector('.location-link, [data-cy="event-location-link"]');
                    if (locationLink) {
                        const locText = locationLink.textContent || '';
                        if (/B[01]\\.[0-9]{2}/.test(locText)) {
                            // Has a practice room location - likely a reservation
                            // But only if the event name suggests it's a booking
                            const displayName = container.querySelector('[data-cy="event-display-name"]');
                            if (displayName) {
                                const nameText = (displayName.textContent || '').trim();
                                // Only count as reservation if it's actually titled "Reservation"
                                if (nameText === 'Reservation') {
                                    return 'Reservation';
                                }
                            }
                        }
                    }

                    // Found the container but no reservation indicators
                    return 'Other';
                }
                container = container.parentElement;
            }

            // Fallback: check immediate parent context
            const parent = element.parentElement;
            if (parent) {
                // Look for Reservation text in a span nearby
                const spans = parent.querySelectorAll('span');
                for (const span of spans) {
                    const text = (span.textContent || '').trim();
                    if (text === 'Reservation') {
                        return 'Reservation';
                    }
                }
            }

            return 'Other';
        }

        // Helper to get the room name from the event container
        function getEventRoom(element) {
            // Find the closest app-as-event container
            let container = element;
            for (let i = 0; i < 10 && container; i++) {
                if (container.tagName === 'APP-AS-EVENT' ||
                    container.classList.contains('as-event-panel') ||
                    container.classList.contains('event-details-expanded')) {

                    // Look for location-link with room pattern
                    const locationLink = container.querySelector('.location-link, [data-cy="event-location-link"]');
                    if (locationLink) {
                        const locText = (locationLink.textContent || '').trim();
                        // Match room patterns like B0.27, B1.09, etc.
                        const roomMatch = locText.match(/B[01]\\.[0-9]{2}/);
                        if (roomMatch) {
                            return roomMatch[0];
                        }
                    }
                    return null;
                }
                container = container.parentElement;
            }
            return null;
        }

        // Find all elements that contain time patterns
        const allElements = document.querySelectorAll('*');
        let currentDate = null;

        for (const element of allElements) {
            // Skip if element has children with text (we want leaf nodes)
            if (element.children.length > 0) {
                const hasTextChild = Array.from(element.children).some(child =>
                    child.textContent.trim().length > 0
                );
                if (hasTextChild) continue;
            }

            const text = element.textContent.trim();

            // Check for date headers
            const dateMatch = text.match(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\\s+(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)\\s+(\\d{4})$/i);
            if (dateMatch) {
                const day = parseInt(dateMatch[2]);
                const month = monthNames.indexOf(dateMatch[3]);
                const year = parseInt(dateMatch[4]);
                currentDate = new Date(year, month, day);
                continue;
            }

            // Check for time ranges like "09:30 - 11:00"
            const timeMatch = text.match(/^(\\d{2}:\\d{2})\\s*[-–]\\s*(\\d{2}:\\d{2})$/);
            if (timeMatch && currentDate) {
                // Check if this event is cancelled
                if (isCancelled(element)) {
                    console.log('Skipping cancelled event:', text);
                    continue;
                }

                const startTime = timeMatch[1];
                const endTime = timeMatch[2];
                const eventTitle = getEventTitle(element);
                const eventRoom = getEventRoom(element);

                const daysDiff = Math.floor((currentDate - today) / (1000 * 60 * 60 * 24));
                if (daysDiff >= 0 && daysDiff <= 7) {
                    events.push({
                        date: currentDate.toISOString().split('T')[0],
                        daysDiff: daysDiff,
                        startTime: startTime,
                        endTime: endTime,
                        title: eventTitle,
                        isReservation: eventTitle === 'Reservation',
                        room: eventRoom
                    });
                }
            }
        }

        return events;
    }""")

    print(f"  [DEBUG] Raw events extracted: {len(events_data)}")

    # Deduplicate events (same date + time can appear multiple times in page text)
    seen = set()
    unique_events = []
    for event in events_data:
        key = f"{event['date']}_{event['startTime']}_{event['endTime']}"
        if key not in seen:
            seen.add(key)
            unique_events.append(event)

    print(f"\n  Found {len(unique_events)} unique events in agenda (deduplicated from {len(events_data)}):")

    # Load ignored events from settings
    ignored_events = load_ignored_events()
    if ignored_events:
        print(f"  ({len(ignored_events)} events marked as ignored - will allow booking over them)")

    events_found = 0
    reservations_found = 0
    ignored_count = 0
    all_reservations = []  # Track all reservations for smart swap feature
    for event in unique_events:
        date_str = event['date']
        start_time = event['startTime']
        end_time = event['endTime']
        days_diff = event['daysDiff']
        is_reservation = event.get('isReservation', False)
        room = event.get('room')  # Room name if available (e.g., "B0.27")

        # Check if this event is ignored
        event_key = f"{date_str}_{start_time}_{end_time}"
        if event_key in ignored_events:
            ignored_count += 1
            day_name = (today + timedelta(days=days_diff)).strftime("%A")
            room_info = f" in {room}" if room else ""
            print(f"  [IGNORED] {date_str} ({day_name}): {start_time} - {end_time}{room_info}")
            continue

        # Parse times to hours
        start_parts = start_time.split(':')
        end_parts = end_time.split(':')
        start_hour = int(start_parts[0]) + int(start_parts[1]) / 60
        end_hour = int(end_parts[0]) + int(end_parts[1]) / 60

        # Calculate the target date
        target_date = datetime.combine(today + timedelta(days=days_diff), datetime.min.time())

        # Add to conflict ranges (reservations also track peak hours and room for same-room gap)
        tracker.add_existing_event(target_date, start_hour, end_hour, is_reservation=is_reservation, room=room)
        events_found += 1
        if is_reservation:
            reservations_found += 1
            # Store reservation details for smart swap feature
            all_reservations.append({
                'date': date_str,
                'startTime': start_time,
                'endTime': end_time,
                'room': room,
                'daysDiff': days_diff
            })

        day_name = target_date.strftime("%A")
        event_marker = "[R]" if is_reservation else "   "
        room_info = f" in {room}" if room else ""
        print(f"  {event_marker} {date_str} ({day_name}): {start_time} - {end_time}{room_info}")

    ignored_str = f", {ignored_count} ignored" if ignored_count > 0 else ""
    print(f"\n  Total events: {events_found} ({reservations_found} reservations, {events_found - reservations_found} other{ignored_str})")

    # Show rolling quota status (28 hours per week)
    used_hours = tracker.get_total_booking_hours()
    remaining_hours = tracker.get_remaining_quota_hours()
    print(f"\n  Rolling quota: {used_hours:.1f}/{MAX_ROLLING_QUOTA_HOURS} hours this week")
    if remaining_hours > 0:
        print(f"  [OK] Can book {remaining_hours:.1f} more hours")
    else:
        print(f"  [FULL] QUOTA FULL - cannot make new bookings until existing ones expire!")

    # Show peak hours summary per day
    if tracker.peak_hours_by_day:
        print("  Peak hours from existing reservations:")
        for date_key, mins in sorted(tracker.peak_hours_by_day.items()):
            print(f"    {date_key}: {mins:.0f}/{MAX_PEAK_HOURS * 60} min")
    print("="*60)
    return events_found, all_reservations


def send_notification(title, message, priority="default"):
    """Send a push notification via ntfy.sh."""
    if not NTFY_ENABLED:
        return

    try:
        url = f"https://ntfy.sh/{NTFY_TOPIC}"
        data = message.encode('utf-8')

        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Title', title)
        req.add_header('Priority', priority)
        req.add_header('Tags', 'musical_keyboard,calendar')

        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                print(f"Notification sent: {title}")
            else:
                print(f"Notification failed: {response.status}")
    except urllib.error.URLError as e:
        print(f"Notification error (network): {e}")
    except Exception as e:
        print(f"Notification error: {e}")


def save_history(bookings_made, events_detected, booking_details):
    """Save run to booking history file."""
    try:
        history = {"runs": []}
        if history_file.exists():
            with open(history_file, 'r') as f:
                history = json.load(f)

        entry = {
            "timestamp": datetime.now().isoformat(),
            "bookings_made": bookings_made,
            "events_detected": events_detected,
            "details": "; ".join(booking_details[:5]) if booking_details else "No bookings made"
        }

        history["runs"].insert(0, entry)
        history["runs"] = history["runs"][:100]  # Keep last 100

        history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(history_file, 'w') as f:
            json.dump(history, f, indent=2)

        print(f"History saved: {bookings_made} bookings, {events_detected} events")

        # Send push notification
        if bookings_made > 0:
            title = f"Booked {bookings_made} room{'s' if bookings_made > 1 else ''}"
            # Format: "2026-02-04 B1.09 13:00 15:00 120" -> "Wed 4 Feb: B1.09 13:00-15:00 (2 hours)"
            formatted = []
            for detail in booking_details[:5]:
                parts = detail.split()
                if len(parts) >= 5:
                    try:
                        date_obj = datetime.strptime(parts[0], "%Y-%m-%d")
                        day_name = date_obj.strftime("%a")
                        day_num = date_obj.day
                        month = date_obj.strftime("%b")
                        room = parts[1]
                        start_time = parts[2]
                        end_time = parts[3]
                        duration_mins = int(parts[4])
                        # Format duration nicely
                        if duration_mins == 60:
                            duration_str = "1 hour"
                        elif duration_mins < 60:
                            duration_str = f"{duration_mins} minutes"
                        elif duration_mins % 60 == 0:
                            hours = duration_mins // 60
                            duration_str = f"{hours} hours"
                        else:
                            hours = duration_mins // 60
                            mins = duration_mins % 60
                            duration_str = f"{hours} hour{'s' if hours > 1 else ''} and {mins} minutes"
                        formatted.append(f"{day_name} {day_num} {month}: {room} {start_time}-{end_time} ({duration_str})")
                    except:
                        formatted.append(detail)
                else:
                    formatted.append(detail)
            message = "\n".join(formatted)
            if len(booking_details) > 5:
                message += f"\n+{len(booking_details) - 5} more"
            send_notification(title, message, priority="default")
        else:
            title = "AsimutBooker ran"
            message = f"No new bookings. {events_detected} existing events detected."
            send_notification(title, message, priority="low")

    except Exception as e:
        print(f"Warning: Could not save history: {e}")


def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Book practice rooms for the week")
    parser.add_argument("--headless", action="store_true", help="Run without browser window")
    parser.add_argument("--target-time", type=str, metavar="HH:MM",
        help="Wait until this time before starting bookings (e.g., 10:00)")
    args = parser.parse_args()

    today = datetime.now().date()

    print("="*60)
    print("ASIMUT WEEK BOOKER")
    print("="*60)
    print(f"Today: {today}")
    print(f"Booking for: {today} to {today + timedelta(days=7)}")
    print(f"Mode: {'Headless' if args.headless else 'Visible browser'}")
    print("="*60)

    # Validate state file before starting browser
    if not state_file.exists():
        print(f"\nERROR: Browser session file not found: {state_file}")
        print("Please run 'python book_week.py' with a visible browser first to complete login.")
        return

    try:
        with open(state_file, 'r') as f:
            state_data = json.load(f)
        if not isinstance(state_data, dict) or 'cookies' not in state_data:
            raise ValueError("Invalid state file format - missing 'cookies' key")
    except json.JSONDecodeError as e:
        print(f"\nERROR: Browser session file is not valid JSON: {e}")
        print(f"Please delete {state_file} and run login setup again.")
        return
    except ValueError as e:
        print(f"\nERROR: Browser session file is invalid: {e}")
        print(f"Please delete {state_file} and run login setup again.")
        return

    tracker = BookingTracker()
    total_booked = 0
    events_detected = 0
    booking_details = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(
            storage_state=str(state_file),
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        # First, scan the agenda to learn about existing events
        events_detected, all_reservations = scan_agenda(page, tracker, today)

        # Check if we can make any bookings at all
        if tracker.is_quota_full():
            used_hours = tracker.get_total_booking_hours()
            print("\n" + "="*60)
            print("QUOTA FULL - SKIPPING ALL BOOKING ATTEMPTS")
            print("="*60)
            print(f"You have {used_hours:.1f}/{MAX_ROLLING_QUOTA_HOURS} hours booked this week.")
            print("Cannot make new bookings until existing ones expire.")
            print("="*60)

            # Save to history and exit early
            history = {"runs": []}
            if history_file.exists():
                try:
                    with open(history_file, 'r') as f:
                        history = json.load(f)
                except:
                    pass
            history["runs"].insert(0, {
                "timestamp": datetime.now().isoformat(),
                "bookings_made": 0,
                "events_detected": events_detected,
                "details": f"Quota full ({used_hours:.1f}/{MAX_ROLLING_QUOTA_HOURS}h)"
            })
            history["runs"] = history["runs"][:100]
            with open(history_file, 'w') as f:
                json.dump(history, f, indent=2)

            send_notification("AsimutBooker - Quota Full",
                            f"Cannot book: {used_hours:.1f}/{MAX_ROLLING_QUOTA_HOURS}h used this week")

            if not args.headless:
                print("\nKeeping browser open for 10 seconds...")
                page.wait_for_timeout(10000)

            context.close()
            browser.close()
            return

        # Navigate directly to AHC practice rooms
        print("\nNavigating to practice rooms (AHC)...")
        page.goto("https://rwcmd.asimut.net/overview?locationGroupId=10", wait_until="networkidle")
        page.wait_for_timeout(3000)

        # Log what date is currently showing
        current_header = page.locator("h1, h2, .title").first.text_content() if page.locator("h1, h2, .title").first.count() > 0 else "Unknown"
        print(f"  Currently showing: {current_header}")

        # Load disabled dates from settings
        disabled_dates = load_disabled_dates()
        if disabled_dates:
            print(f"\n  Disabled dates (will skip): {sorted(disabled_dates)}")

        # Load time preferences
        time_prefs = load_time_preferences()
        if time_prefs["enabled"]:
            print(f"\n  Time preferences: Prioritizing {int(time_prefs['start_hour']):02d}:00 - {int(time_prefs['end_hour']):02d}:00")
            if time_prefs["strict_mode"]:
                print(f"  Strict mode: ON (only booking preferred times)")

        # Load booking strategy settings
        booking_strategy = load_booking_strategy()
        reverse_date_order = booking_strategy["reverse_date_order"]
        if reverse_date_order:
            print(f"\n  Booking strategy: REVERSE ORDER (furthest dates first)")

        # Build list of enabled dates for smart redistribution
        enabled_dates = []
        for d in range(0, 8):
            check_date = today + timedelta(days=d)
            if not is_date_disabled(check_date, disabled_dates):
                enabled_dates.append(check_date)

        remaining_quota = tracker.get_remaining_quota_hours()
        ideal_per_day = MAX_ROLLING_QUOTA_HOURS / len(enabled_dates) if enabled_dates else 0

        print(f"\n  Smart redistribution calculation:")
        print(f"    Enabled days: {len(enabled_dates)}/8")
        print(f"    Remaining quota: {remaining_quota:.1f}h")
        print(f"    Ideal hours/day: {ideal_per_day:.1f}h")
        print(f"    Current hours per day:")
        for d in enabled_dates:
            day_hours = tracker.get_hours_for_day(d)
            deficit = ideal_per_day - day_hours
            status = "✓" if day_hours >= ideal_per_day else f"needs +{deficit:.1f}h"
            print(f"      {d.strftime('%Y-%m-%d')} ({d.strftime('%a')}): {day_hours:.1f}h [{status}]")

        # Get current time for filtering today's slots
        current_time_hour = datetime.now().hour + datetime.now().minute / 60.0

        # Determine the order to process days based on strategy
        # Filter out disabled dates AND days with no bookable rooms (horizon not reached)
        enabled_day_order = []
        skipped_horizon_days = []
        for d in range(0, 8):
            check_date = today + timedelta(days=d)
            if is_date_disabled(check_date, disabled_dates):
                continue
            # Check if any room is bookable on this day
            any_bookable, horizon_reason = is_any_room_bookable_on_day(check_date)
            if any_bookable:
                enabled_day_order.append(d)
            else:
                skipped_horizon_days.append((d, check_date.strftime('%Y-%m-%d'), horizon_reason))

        if skipped_horizon_days:
            print(f"\n  Days skipped (horizon not reached):")
            for d, date_str, reason in skipped_horizon_days:
                print(f"    Day {d} ({date_str}): {reason}")

        if reverse_date_order:
            # Process from furthest enabled day down to nearest (furthest first)
            day_order = sorted(enabled_day_order, reverse=True)
            if day_order:
                first_day = day_order[0]
                print(f"\n  Navigating to day {first_day} first (reverse order strategy)...")
                for nav_step in range(first_day):
                    clicked = False
                    for attempt in range(3):
                        try:
                            page.keyboard.press("Escape")
                            page.wait_for_timeout(200)
                            page.evaluate("window.scrollTo(0, 0)")
                            page.wait_for_timeout(200)
                            arrow = page.locator("mat-icon").filter(has_text="chevron_right").first
                            if arrow.count() > 0 and arrow.is_visible():
                                arrow.click(force=True)
                                page.wait_for_timeout(1000)
                                clicked = True
                                break
                        except Exception as e:
                            pass
                    if not clicked:
                        print(f"  [DEBUG] Forward navigation step {nav_step + 1} failed")
                current_calendar_day = first_day  # Calendar is now showing first enabled day
            else:
                print(f"\n  No bookable days available!")
                current_calendar_day = 0
        else:
            # Normal order: process enabled days from nearest to furthest
            day_order = sorted(enabled_day_order)
            current_calendar_day = 0  # Calendar starts on today

        # Wait for target time if specified (prep is done, ready to book)
        if args.target_time:
            # =================================================================
            # HORIZON SNIPE: Check for slots about to become bookable
            # =================================================================
            # Before waiting, check if there's a slot that will become bookable
            # right at target time. If so, we can "snipe" it by opening the
            # booking form early and clicking Save at the exact moment.

            # Get current slot data to look for snipe candidates
            print("\n" + "="*60)
            print("CHECKING FOR HORIZON SNIPE OPPORTUNITIES")
            print("="*60)

            snipe_target_date = today + timedelta(days=day_order[0]) if day_order else today
            available_data = get_available_slots(page)
            snipe_candidate, seconds_until_bookable = find_horizon_snipe_candidate(
                available_data, snipe_target_date, tracker, time_prefs
            )

            if snipe_candidate:
                print(f"  [SNIPE] Found candidate: {snipe_candidate['room']} at {snipe_candidate['start_hour']:.2f}")
                print(f"  [SNIPE] Becomes bookable in {seconds_until_bookable:.1f} seconds")
                print(f"  [SNIPE] Bookable at: {snipe_candidate['bookable_from'].strftime('%H:%M:%S')}")

                # Attempt the snipe
                snipe_success = try_horizon_snipe(
                    page, snipe_candidate, snipe_target_date, tracker, day_order[0]
                )

                if snipe_success:
                    total_booked += 1
                    booking_details.append(
                        f"{snipe_target_date.strftime('%Y-%m-%d')} {snipe_candidate['room']} "
                        f"{int(snipe_candidate['start_hour']):02d}:{int((snipe_candidate['start_hour'] % 1) * 60):02d} "
                        f"{int(snipe_candidate['start_hour'] + 0.5):02d}:{int(((snipe_candidate['start_hour'] + 0.5) % 1) * 60):02d} "
                        f"{MIN_BOOKING_MINUTES}"
                    )
                    print(f"\n  [SNIPE] Horizon snipe successful!")

                    # Navigate back to calendar for normal booking flow
                    go_back(page, day_order[0])
                    page.wait_for_timeout(1000)
                else:
                    print(f"\n  [SNIPE] Horizon snipe failed, continuing with normal flow...")
            else:
                print(f"  No snipe candidates found (no slots becoming bookable within 3 minutes)")

            print("="*60)

            # Now wait for remaining time (if any) and refresh for normal booking
            wait_until_target_time(args.target_time, page)

            # =================================================================
            # REFRESH PAGE FOR UPDATED SLOT AVAILABILITY
            # =================================================================
            print("\n" + "="*60)
            print("REFRESHING PAGE FOR UPDATED SLOT AVAILABILITY")
            print("="*60)
            pre_refresh_time = datetime.now()
            print(f"  [DEBUG] Pre-refresh time: {pre_refresh_time.strftime('%H:%M:%S.%f')[:-3]}")
            print(f"  [DEBUG] Current URL: {page.url[:80]}...")

            # Store current calendar day position before refresh
            pre_refresh_calendar_day = current_calendar_day
            print(f"  [DEBUG] Calendar position before refresh: day {pre_refresh_calendar_day}")

            # Refresh the current page to get fresh slot data
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(1500)

            post_refresh_time = datetime.now()
            refresh_duration = (post_refresh_time - pre_refresh_time).total_seconds()
            print(f"  [DEBUG] Post-refresh time: {post_refresh_time.strftime('%H:%M:%S.%f')[:-3]}")
            print(f"  [DEBUG] Refresh took: {refresh_duration:.2f}s")
            print(f"  [DEBUG] Post-refresh URL: {page.url[:80]}...")

            # Verify we're still on the correct day after refresh
            print(f"  [DEBUG] Calendar position after refresh: day {current_calendar_day} (unchanged)")

            # Debug: Sample what slots look like now
            try:
                sample_slots = page.evaluate("""() => {
                    const locationDays = document.querySelectorAll('.location-day');
                    if (!locationDays.length) return 'No location-day elements found';

                    // Find first room with gaps
                    for (const day of locationDays) {
                        const roomName = day.closest('.location-row')?.querySelector('.room-name-line')?.textContent?.trim();
                        const gaps = day.querySelectorAll('.gap');
                        if (gaps.length > 0 && roomName) {
                            const firstGap = gaps[0];
                            const style = firstGap.getAttribute('style') || '';
                            const leftMatch = style.match(/left:\\s*([\\d.]+)%/);
                            const widthMatch = style.match(/width:\\s*([\\d.]+)%/);
                            if (leftMatch && widthMatch) {
                                const left = parseFloat(leftMatch[1]);
                                const startHour = 7.5 + (left / 100) * 15;  // 7:30 to 22:30 = 15 hours
                                const startH = Math.floor(startHour);
                                const startM = Math.round((startHour % 1) * 60);
                                return `First slot: ${roomName} starts at ${startH}:${startM.toString().padStart(2, '0')} (left=${left.toFixed(1)}%)`;
                            }
                        }
                    }
                    return 'Could not extract slot times';
                }""")
                print(f"  [DEBUG] {sample_slots}")
            except Exception as e:
                print(f"  [DEBUG] Could not sample slots: {e}")

            print("="*60)

        # =================================================================
        # PROCESS PENDING BOOKING EXTENSIONS
        # =================================================================
        # Before making new bookings, try to extend any bookings that were
        # made at the horizon edge and can now be extended.
        extendable_bookings = load_extendable_bookings()
        if extendable_bookings:
            print("\n" + "="*60)
            print("PROCESSING BOOKING EXTENSIONS")
            print("="*60)
            print(f"Found {len(extendable_bookings)} potentially extendable booking(s)")

            extensions_made = 0
            for booking in extendable_bookings:
                success, new_end, message = try_extend_booking(page, booking, tracker)
                if success:
                    extensions_made += 1
                    booking_details.append(
                        f"EXTENDED: {booking['room']} {booking['date']} "
                        f"{booking['startTime']}-{new_end}"
                    )
                else:
                    print(f"  Skipped {booking['room']} {booking['date']} {booking['startTime']}: {message}")

            if extensions_made > 0:
                total_booked += extensions_made
                print(f"\nExtended {extensions_made} booking(s)")

            print("="*60)

            # Navigate back to booking overview after extensions
            print("\nNavigating back to practice rooms...")
            page.goto("https://rwcmd.asimut.net/overview?locationGroupId=10", wait_until="networkidle")
            page.wait_for_timeout(2000)

            # Reset calendar position - overview page shows today (day 0)
            current_calendar_day = 0

        # Process each day in the determined order
        for day_index, days_ahead in enumerate(day_order):
            target_date = datetime.combine(today + timedelta(days=days_ahead), datetime.min.time())
            day_name = target_date.strftime("%A")

            print(f"\n{'#'*60}")
            print(f"# DAY {days_ahead}: {target_date.strftime('%Y-%m-%d')} ({day_name})")
            print(f"{'#'*60}")

            # Calculate dynamic target for this specific day based on current distribution
            target_hours, max_bookings_per_day = calculate_target_hours_for_day(
                target_date, enabled_dates, tracker
            )
            current_day_hours = tracker.get_hours_for_day(target_date)
            print(f"  [Smart redistribution] Current: {current_day_hours:.1f}h, Target: +{target_hours:.1f}h, Max bookings: {max_bookings_per_day}")

            day_peak_used = tracker.get_peak_used_for_day(target_date)
            print(f"  [DEBUG] Session stats: {total_booked} bookings so far, peak for this day: {day_peak_used:.0f}/{MAX_PEAK_HOURS * 60}min")

            # Navigate to the correct day based on current calendar position
            # Calculate how many steps we need to navigate
            steps_needed = days_ahead - current_calendar_day

            if steps_needed != 0:
                direction = "forward" if steps_needed > 0 else "backward"
                arrow_icon = "chevron_right" if steps_needed > 0 else "chevron_left"
                steps_abs = abs(steps_needed)

                print(f"  Navigating {direction} {steps_abs} day(s)...")

                for step in range(steps_abs):
                    clicked = False
                    for attempt in range(5):
                        try:
                            # Dismiss any dialogs/modals
                            page.keyboard.press("Escape")
                            page.wait_for_timeout(300)

                            # Scroll to absolute top to ensure arrow is visible
                            page.evaluate("window.scrollTo(0, 0)")
                            page.wait_for_timeout(500)

                            # Wait for page to stabilize
                            try:
                                page.wait_for_load_state("networkidle", timeout=3000)
                            except:
                                pass  # Continue even if timeout

                            # Debug: Check current URL and page state
                            current_url = page.url
                            if attempt == 0 and step == 0:
                                print(f"  [DEBUG] Current URL: {current_url[:60]}...")

                            # Find and click the appropriate arrow
                            arrow = None

                            # Method 1: mat-icon with arrow text
                            try:
                                arrow = page.locator("mat-icon").filter(has_text=arrow_icon).first
                                arrow_count = arrow.count()
                                arrow_visible = arrow.is_visible() if arrow_count > 0 else False
                                if arrow_count > 0 and arrow_visible:
                                    arrow.click(force=True)
                                    page.wait_for_timeout(1000)
                                    clicked = True
                                    break
                            except Exception as e:
                                if attempt == 0:
                                    print(f"  [DEBUG] Method 1 failed: {e}")

                            # Method 2: Button containing arrow
                            try:
                                arrow = page.locator(f"button:has(mat-icon:text('{arrow_icon}'))").first
                                if arrow.count() > 0 and arrow.is_visible():
                                    arrow.click(force=True)
                                    page.wait_for_timeout(1000)
                                    clicked = True
                                    break
                            except:
                                pass

                            # Method 3: Any element with arrow text
                            try:
                                arrow = page.locator(f"text={arrow_icon}").first
                                if arrow.count() > 0 and arrow.is_visible():
                                    arrow.click(force=True)
                                    page.wait_for_timeout(1000)
                                    clicked = True
                                    break
                            except:
                                pass

                            # After 2 failed attempts, try reloading the page and navigating from scratch
                            if attempt == 2:
                                print(f"  [DEBUG] Reloading calendar page after failed navigation...")
                                page.goto("https://rwcmd.asimut.net/overview?locationGroupId=10", wait_until="networkidle")
                                page.wait_for_timeout(1000)
                                # Navigate to the target day from today
                                print(f"  [DEBUG] Navigating to day {days_ahead} from today...")
                                for click_num in range(days_ahead):
                                    try:
                                        page.evaluate("window.scrollTo(0, 0)")
                                        page.wait_for_timeout(200)
                                        nav_arrow = page.locator("mat-icon").filter(has_text="chevron_right").first
                                        if nav_arrow.count() > 0:
                                            nav_arrow.click(force=True)
                                            page.wait_for_timeout(1000)
                                    except Exception as e:
                                        print(f"  [DEBUG] Forward click {click_num + 1} failed: {e}")
                                current_calendar_day = days_ahead  # We jumped directly
                                clicked = True
                                break

                            print(f"    Arrow not found (attempt {attempt + 1}), waiting...")
                            page.wait_for_timeout(1000)
                        except Exception as e:
                            print(f"    Arrow click failed: {e}")
                            page.wait_for_timeout(1000)

                    if not clicked:
                        print(f"  [DEBUG] Navigation step {step + 1} failed after all attempts")

                # Update current calendar day after all navigation steps
                current_calendar_day = days_ahead

                if steps_needed != 0:
                    print(f"  [DEBUG] Navigation complete, now on day {current_calendar_day}")
                    page.wait_for_timeout(1000)
            else:
                print(f"  Already on correct day (day {days_ahead}) - no navigation needed")

            # Debug: Log current time when scanning slots for this day
            scan_time = datetime.now()
            print(f"  [DEBUG] Slot scan time: {scan_time.strftime('%H:%M:%S')}")

            # Verify the actual displayed date
            displayed_date = page.evaluate("""() => {
                const header = document.querySelector('h1, h2, .date-header, [class*="title"]');
                if (header) return header.textContent;
                // Look for date pattern in page
                const bodyText = document.body.innerText;
                const match = bodyText.match(/(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\\s+(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)/i);
                return match ? match[0] : null;
            }""")
            print(f"  Displayed date: {displayed_date}")

            # Get available slots
            available_data = get_available_slots(page)

            # Debug: Show earliest slot start times for 7-day horizon rooms
            # This helps verify the page refresh is working correctly
            seven_day_rooms = [r for r, h in ROOM_HORIZONS.items() if h == 7]
            earliest_by_room = {}
            for room_data in available_data:
                room_name = room_data["room"]
                if room_name in seven_day_rooms and room_data["slots"]:
                    earliest_start = min(s['startHour'] for s in room_data["slots"])
                    earliest_h = int(earliest_start)
                    earliest_m = int((earliest_start % 1) * 60)
                    earliest_by_room[room_name] = f"{earliest_h:02d}:{earliest_m:02d}"
            if earliest_by_room:
                print(f"  [DEBUG] Earliest slots for 7-day horizon rooms: {earliest_by_room}")
                # Calculate what time SHOULD be the earliest based on current time
                expected_earliest = scan_time - timedelta(days=7)
                expected_earliest_str = expected_earliest.strftime('%H:%M')
                print(f"  [DEBUG] Expected earliest (now - 7 days): ~{expected_earliest_str} on target date")

            # Build sorted slot list - prefer longer slots
            all_slots = []
            horizon_skipped = {}  # Track rooms skipped due to horizon
            for room_data in available_data:
                room_name = room_data["room"]
                if room_name not in PRIORITY_ROOMS:
                    continue

                room_priority = PRIORITY_ROOMS.index(room_name)

                for slot in room_data["slots"]:
                    slot_duration = slot['endHour'] - slot['startHour']
                    if slot_duration * 60 < MIN_BOOKING_MINUTES:
                        continue  # Too short

                    # For today (days_ahead == 0), skip slots that have already started
                    if days_ahead == 0 and slot['startHour'] <= current_time_hour:
                        continue  # Slot is in the past

                    # Check if room is available based on booking horizon
                    is_available, horizon_reason = is_room_available_to_book(room_name, target_date, slot['startHour'])
                    if not is_available:
                        if room_name not in horizon_skipped:
                            horizon_skipped[room_name] = horizon_reason
                        continue

                    # Prefer longer slots (at least 1 hour)
                    is_good_slot = slot_duration >= 1.0

                    all_slots.append({
                        "room": room_name,
                        "start_hour": slot['startHour'],
                        "end_hour": slot['endHour'],
                        "duration": slot_duration,
                        "room_priority": room_priority,
                        "is_good": is_good_slot,
                        "click_x": slot["clickX"],
                        "click_y": slot["clickY"]
                    })

            # Log which rooms were skipped due to horizon
            if horizon_skipped:
                print(f"  [DEBUG] Rooms skipped (not yet available): {', '.join(f'{r} ({reason})' for r, reason in horizon_skipped.items())}")

            # Sort: preferred times first, then good slots (longer), then by time, then by room priority
            all_slots.sort(key=lambda s: (
                0 if is_preferred_time(s['start_hour'], time_prefs) else 1,
                not s['is_good'],
                s['start_hour'],
                s['room_priority']
            ))

            if not all_slots:
                print("  No available slots found")
                continue

            print(f"  Found {len(all_slots)} potential slots")
            # Show first 10 slots for debugging
            if all_slots:
                slot_info = [(s['room'], f"{s['start_hour']:.2f}-{s['end_hour']:.2f}") for s in all_slots[:10]]
                print(f"  [DEBUG] First 10 slots: {slot_info}")

            # Try to book slots
            day_booked = 0
            attempts = 0
            max_attempts = 15  # Reduce to avoid too many failed attempts

            # Log peak quota status for this specific day
            remaining_peak = tracker.get_remaining_peak_minutes(target_date)
            day_peak_used = tracker.get_peak_used_for_day(target_date)
            print(f"  [DEBUG] Peak quota for {target_date.strftime('%Y-%m-%d')}: {day_peak_used:.0f}/{MAX_PEAK_HOURS * 60} min used, {remaining_peak:.0f} min remaining")

            # Adjust slots to avoid conflicts AND peak zone if quota exceeded
            adjusted_slots = []
            conflicts = tracker.conflict_ranges.get(target_date.strftime('%Y-%m-%d'), [])

            if conflicts:
                print(f"  [DEBUG] Known conflicts for this day: {[(f'{c[0]:.2f}-{c[1]:.2f}') for c in conflicts]}")

            slots_removed = 0
            slots_adjusted = 0
            peak_adjusted = 0
            weekly_adjusted = 0
            conflict_removed = 0

            def split_slot_around_blocks(slot_start, slot_end, blocked_ranges):
                """Split a slot into valid sub-slots that don't overlap with blocked ranges.

                Returns list of (start, end) tuples representing usable time segments.
                """
                if not blocked_ranges:
                    return [(slot_start, slot_end)]

                # Sort blocked ranges by start time
                sorted_blocks = sorted(blocked_ranges, key=lambda x: x[0])

                # Find all usable gaps
                usable = []
                current_start = slot_start

                for b_start, b_end in sorted_blocks:
                    # If there's usable time before this block
                    if b_start > current_start:
                        gap_start = current_start
                        gap_end = min(b_start, slot_end)
                        if gap_end > gap_start and (gap_end - gap_start) * 60 >= MIN_BOOKING_MINUTES:
                            usable.append((gap_start, gap_end))

                    # Move past this block
                    current_start = max(current_start, b_end)

                    # If we've moved past the slot end, stop
                    if current_start >= slot_end:
                        break

                # Check for usable time after all blocks
                if current_start < slot_end and (slot_end - current_start) * 60 >= MIN_BOOKING_MINUTES:
                    usable.append((current_start, slot_end))

                return usable

            for s in all_slots:
                slot_start = s['start_hour']
                slot_end = s['end_hour']
                original_start = slot_start
                original_end = slot_end

                # Step 1: Split slot around conflicts (get all usable sub-slots)
                conflict_blocks = [(c_start, c_end) for c_start, c_end in conflicts
                                   if slot_start < c_end and slot_end > c_start]
                usable_segments = split_slot_around_blocks(slot_start, slot_end, conflict_blocks)

                if not usable_segments:
                    conflict_removed += 1
                    continue

                # Step 1.5: For each usable segment, also split around same-room gap blocks
                room_blocked = tracker.get_same_room_blocked_ranges(target_date, s['room'])
                final_segments = []
                for seg_start, seg_end in usable_segments:
                    room_blocks = [(b_start, b_end) for b_start, b_end in room_blocked
                                   if seg_start < b_end and seg_end > b_start]
                    sub_segments = split_slot_around_blocks(seg_start, seg_end, room_blocks)
                    final_segments.extend(sub_segments)

                if not final_segments:
                    conflict_removed += 1
                    continue

                # Add ALL valid segments as separate slots (not just the longest one)
                # This ensures we don't skip earlier bookable time
                for seg_start, seg_end in final_segments:
                    # Step 2: Adjust each segment for peak hours quota (only on weekdays)
                    adj_start, adj_end, was_adjusted, reason = adjust_slot_for_peak_quota(
                        seg_start, seg_end, target_date, remaining_peak
                    )

                    if adj_start is None:
                        # Slot should be skipped (fully in peak zone with no quota)
                        slots_removed += 1
                        continue

                    if was_adjusted:
                        seg_start = adj_start
                        seg_end = adj_end
                        peak_adjusted += 1

                    # Step 3: Adjust for weekly quota (trim if exceeds remaining hours)
                    remaining_weekly = tracker.get_remaining_quota_hours()
                    adj_start, adj_end, was_weekly_adj, weekly_reason = adjust_slot_for_weekly_quota(
                        seg_start, seg_end, remaining_weekly
                    )

                    if adj_start is None:
                        # Not enough weekly quota remaining
                        slots_removed += 1
                        continue

                    if was_weekly_adj:
                        seg_start = adj_start
                        seg_end = adj_end
                        weekly_adjusted += 1

                    # Final validation and add as new slot
                    if seg_start < seg_end and (seg_end - seg_start) * 60 >= MIN_BOOKING_MINUTES:
                        new_slot = s.copy()
                        new_slot['start_hour'] = seg_start
                        new_slot['duration'] = seg_end - seg_start
                        new_slot['end_hour'] = seg_end
                        adjusted_slots.append(new_slot)
                        if seg_start != original_start or seg_end != original_end:
                            slots_adjusted += 1
                    else:
                        slots_removed += 1

            if conflict_removed > 0 or slots_removed > 0 or slots_adjusted > 0 or peak_adjusted > 0 or weekly_adjusted > 0:
                print(f"  [DEBUG] Slot adjustment: {conflict_removed} conflict-removed, {slots_removed} removed, {slots_adjusted} adjusted, {peak_adjusted} peak-adjusted, {weekly_adjusted} weekly-adjusted")

            all_slots = adjusted_slots

            # Filter out non-preferred slots if strict mode is enabled
            if time_prefs["enabled"] and time_prefs["strict_mode"]:
                before_count = len(all_slots)
                all_slots = [s for s in all_slots if is_preferred_time(s['start_hour'], time_prefs)]
                filtered_count = before_count - len(all_slots)
                if filtered_count > 0:
                    print(f"  [DEBUG] Strict mode: Filtered out {filtered_count} non-preferred slots")

            all_slots.sort(key=lambda s: (
                0 if is_preferred_time(s['start_hour'], time_prefs) else 1,
                not s.get('is_good', False),
                s['start_hour'],
                s['room_priority']
            ))

            print(f"  [DEBUG] Starting booking attempts. Max per day: {max_bookings_per_day}, slots available: {len(all_slots)}")

            while day_booked < max_bookings_per_day and attempts < max_attempts:
                # Check if we have valid slots to try
                valid_slots = [s for s in all_slots if not tracker.overlaps_conflict(
                    target_date, s['start_hour'], s['start_hour'] + min(s['duration'], MAX_BOOKING_HOURS)
                )]

                if not valid_slots:
                    print(f"  [DEBUG] No more valid slots (all_slots={len(all_slots)}, conflicts filtered all)")
                    print("  No more valid slots available")
                    break

                slot = valid_slots[0]  # Try the best remaining slot
                all_slots.remove(slot)  # Remove from list so we don't retry
                attempts += 1

                print(f"  [DEBUG] Attempt {attempts}/{max_attempts}: {slot['room']} {slot['start_hour']:.2f}-{slot['end_hour']:.2f} (duration: {slot['duration']:.2f}h)")

                try:
                    booked = try_book_slot(page, slot, target_date, tracker, days_ahead)
                except Exception as e:
                    print(f"  Error booking slot: {e}")
                    # Check if browser is still open
                    try:
                        page.evaluate("1+1")  # Simple check
                    except:
                        print("  Browser was closed - exiting")
                        break
                    continue

                if booked:
                    day_booked += 1
                    total_booked += 1
                    print(f"  [DEBUG] Booking successful! Day total: {day_booked}, session total: {total_booked}")

                    # Record booking detail with start, end, and duration
                    room_name = slot['room']
                    start_h = int(slot['start_hour'])
                    start_m = int((slot['start_hour'] % 1) * 60)
                    # Calculate actual booked end time (capped at MAX_BOOKING_HOURS)
                    booked_duration = min(slot['duration'], MAX_BOOKING_HOURS)
                    booked_end_hour = slot['start_hour'] + booked_duration
                    end_h = int(booked_end_hour)
                    end_m = int((booked_end_hour % 1) * 60)
                    duration_mins = int(booked_duration * 60)
                    booking_details.append(f"{target_date.strftime('%Y-%m-%d')} {room_name} {start_h:02d}:{start_m:02d} {end_h:02d}:{end_m:02d} {duration_mins}")

                    # After successful booking, navigate back to calendar and refresh slots
                    page.wait_for_timeout(1000)

                    # Navigate back to calendar (we're on the booking confirmation page)
                    print("  [DEBUG] Navigating back to calendar...")
                    go_back(page, days_ahead)
                    page.wait_for_timeout(1000)

                    # Re-fetch available slots since the calendar has changed
                    print("  [DEBUG] Refreshing slot data after successful booking...")
                    available_data = get_available_slots(page)
                    total_raw_slots = sum(len(r["slots"]) for r in available_data)
                    print(f"  [DEBUG] Raw slots from page: {total_raw_slots} (from {len(available_data)} rooms)")

                    # Recalculate remaining peak quota after booking for this day
                    remaining_peak = tracker.get_remaining_peak_minutes(target_date)
                    print(f"  [DEBUG] Updated peak quota for {target_date.strftime('%Y-%m-%d')}: {remaining_peak:.0f} min remaining")

                    # Get updated conflict list (includes the booking we just made)
                    conflicts = tracker.conflict_ranges.get(target_date.strftime('%Y-%m-%d'), [])

                    # Rebuild slot list with conflict and peak adjustment
                    all_slots = []
                    for room_data in available_data:
                        room_name = room_data["room"]
                        if room_name not in PRIORITY_ROOMS:
                            continue

                        room_priority = PRIORITY_ROOMS.index(room_name)

                        for slot_data in room_data["slots"]:
                            slot_start = slot_data['startHour']
                            slot_end = slot_data['endHour']
                            slot_duration = slot_end - slot_start

                            if slot_duration * 60 < MIN_BOOKING_MINUTES:
                                continue

                            # Check room horizon availability
                            is_available, _ = is_room_available_to_book(room_name, target_date, slot_start)
                            if not is_available:
                                continue

                            # Step 1: Check if slot overlaps with any conflict
                            for c_start, c_end in conflicts:
                                if slot_start < c_end and slot_end > c_start:
                                    # Slot overlaps with conflict - try to use the part after the conflict
                                    if c_end < slot_end:
                                        slot_start = c_end
                                    else:
                                        slot_start = slot_end  # Will be filtered out below
                                        break

                            # Skip if slot was fully consumed by conflict
                            if slot_start >= slot_end or (slot_end - slot_start) * 60 < MIN_BOOKING_MINUTES:
                                continue

                            slot_duration = slot_end - slot_start

                            # Step 2: Apply peak adjustment
                            adj_start, adj_end, was_adjusted, reason = adjust_slot_for_peak_quota(
                                slot_start, slot_end, target_date, remaining_peak
                            )

                            if adj_start is None:
                                continue  # Skip slots fully in peak zone with no quota

                            if was_adjusted:
                                slot_start = adj_start
                                slot_end = adj_end
                                slot_duration = slot_end - slot_start

                            # Step 3: Apply weekly quota adjustment
                            remaining_weekly = tracker.get_remaining_quota_hours()
                            adj_start, adj_end, was_weekly_adj, weekly_reason = adjust_slot_for_weekly_quota(
                                slot_start, slot_end, remaining_weekly
                            )

                            if adj_start is None:
                                continue  # Not enough weekly quota remaining

                            if was_weekly_adj:
                                slot_start = adj_start
                                slot_end = adj_end
                                slot_duration = slot_end - slot_start

                            is_good_slot = slot_duration >= 1.0

                            all_slots.append({
                                "room": room_name,
                                "start_hour": slot_start,
                                "end_hour": slot_end,
                                "duration": slot_duration,
                                "room_priority": room_priority,
                                "is_good": is_good_slot,
                                "click_x": slot_data["clickX"],
                                "click_y": slot_data["clickY"]
                            })

                    # Apply strict mode filter if enabled
                    if time_prefs["enabled"] and time_prefs["strict_mode"]:
                        all_slots = [s for s in all_slots if is_preferred_time(s['start_hour'], time_prefs)]

                    all_slots.sort(key=lambda s: (
                        0 if is_preferred_time(s['start_hour'], time_prefs) else 1,
                        not s['is_good'],
                        s['start_hour'],
                        s['room_priority']
                    ))
                    print(f"  [DEBUG] Rebuilt slot list: {len(all_slots)} slots remaining")

            print(f"\n  Day summary: {day_booked} bookings made, {attempts} attempts total")

        # Final summary
        print("\n" + "="*60)
        print("BOOKING COMPLETE")
        print("="*60)
        print(f"Total bookings made: {total_booked}")
        if tracker.peak_hours_by_day:
            print("Peak hours used (per day):")
            for date_key, mins in sorted(tracker.peak_hours_by_day.items()):
                print(f"  {date_key}: {mins:.0f}/{MAX_PEAK_HOURS * 60} min")
        else:
            print("Peak hours used: 0 minutes")
        print("\nAll bookings:")
        for room, date, start, end in tracker.bookings:
            start_str = f"{int(start):02d}:{int((start % 1) * 60):02d}"
            end_str = f"{int(end):02d}:{int((end % 1) * 60):02d}"
            print(f"  {date.strftime('%Y-%m-%d')} {room} {start_str}-{end_str}")
        print("="*60)

        # Save run to history
        save_history(total_booked, events_detected, booking_details)

        if not args.headless:
            print("\nKeeping browser open for 30 seconds to verify...")
            page.wait_for_timeout(30000)

        context.close()
        browser.close()


if __name__ == "__main__":
    main()
