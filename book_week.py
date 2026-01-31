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
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

state_file = Path("data/browser_state/state.json")
history_file = Path("data/booking_history.json")

# ntfy.sh notification settings
# Change this to your own unique topic name (like "asimut-yourname-secret123")
NTFY_TOPIC = "asimut-loxty"
NTFY_ENABLED = True  # Set to False to disable notifications

# Booking rules
MAX_BOOKING_HOURS = 2
MIN_BOOKING_MINUTES = 30
PREFERRED_MIN_MINUTES = 60  # Prefer slots at least 1 hour
PEAK_START = 9   # 9am
PEAK_END = 16    # 4pm
MAX_PEAK_HOURS = 2
SAME_ROOM_GAP_MINUTES = 60
MAX_BOOKINGS_PER_DAY = 3  # Limit per day to spread across week

# Priority rooms in order (including all rooms with different booking horizons)
PRIORITY_ROOMS = [
    "B0.29", "B1.09", "B0.11", "B1.16", "B0.15", "B0.13", "B0.14", "B0.27",
    "B1.14", "B1.17", "B1.20", "B1.18", "B1.21", "B1.19",
    "B1.06", "B1.07", "B1.08", "B1.10", "B1.11", "B1.15",
    "B0.23", "B0.24"  # Added missing rooms
]

# Room booking horizons (how many days in advance each room can be booked)
# Rooms become available exactly X days later, by the minute
ROOM_HORIZONS = {
    # 3 days in advance
    "B1.09": 3,
    "B1.16": 3,
    # 5 days in advance
    "B0.23": 5,
    "B0.24": 5,
    "B0.27": 5,
    "B0.29": 5,
    "B1.06": 5,
    "B1.07": 5,
    "B1.08": 5,
    "B1.10": 5,
    "B1.11": 5,
    "B1.14": 5,
    "B1.15": 5,
    "B1.17": 5,
    "B1.18": 5,
    "B1.19": 5,
    "B1.20": 5,
    "B1.21": 5,
    # All other rooms default to 7 days
}
DEFAULT_HORIZON = 7  # Default for rooms not in ROOM_HORIZONS


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
    slot_datetime = datetime.combine(target_date.date(), datetime.min.time())
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


class BookingTracker:
    """Tracks bookings and enforces rules."""

    def __init__(self):
        self.bookings = []  # List of (room, date, start_hour, end_hour)
        self.conflict_ranges = {}  # Per-day conflict ranges: {date: [(start, end), ...]}
        self.peak_hours_used = 0  # Total minutes of peak time used (from existing + new bookings)

    def add_existing_event(self, date, start_hour, end_hour):
        """Record an existing event from agenda scan (tracks peak hours + conflicts)."""
        # Add to conflict ranges
        date_key = date.strftime('%Y-%m-%d')
        if date_key not in self.conflict_ranges:
            self.conflict_ranges[date_key] = []
        self.conflict_ranges[date_key].append((start_hour, end_hour))

        # Track peak hours if this is a weekday event during peak hours
        if date.weekday() < 5:  # Monday-Friday
            overlap_start = max(start_hour, PEAK_START)
            overlap_end = min(end_hour, PEAK_END)
            if overlap_end > overlap_start:
                peak_mins = (overlap_end - overlap_start) * 60
                self.peak_hours_used += peak_mins
                print(f"  [Tracker] Added conflict: {date_key} {start_hour:.2f}-{end_hour:.2f} (+{peak_mins:.0f}min peak)")
                return

        print(f"  [Tracker] Added conflict: {date_key} {start_hour:.2f}-{end_hour:.2f}")

    def add_booking(self, room, date, start_hour, end_hour):
        """Record a successful booking."""
        self.bookings.append((room, date, start_hour, end_hour))

        # Update peak hours if applicable
        if date.weekday() < 5:  # Monday-Friday
            overlap_start = max(start_hour, PEAK_START)
            overlap_end = min(end_hour, PEAK_END)
            if overlap_end > overlap_start:
                self.peak_hours_used += (overlap_end - overlap_start) * 60

        print(f"  [Tracker] Added booking: {room} {date.strftime('%Y-%m-%d')} {start_hour:.2f}-{end_hour:.2f}")
        print(f"  [Tracker] Total bookings: {len(self.bookings)}, Peak hours used: {self.peak_hours_used:.0f} min")

    def add_conflict(self, date, start_hour, end_hour):
        """Record a discovered conflict range (during booking, not from agenda)."""
        date_key = date.strftime('%Y-%m-%d')
        if date_key not in self.conflict_ranges:
            self.conflict_ranges[date_key] = []
        self.conflict_ranges[date_key].append((start_hour, end_hour))
        print(f"  [Tracker] Added conflict: {date_key} {start_hour:.2f}-{end_hour:.2f}")

    def get_remaining_peak_minutes(self):
        """Get remaining peak minutes available."""
        return max(0, MAX_PEAK_HOURS * 60 - self.peak_hours_used)

    def is_peak_quota_exceeded(self):
        """Check if peak hours quota is exceeded."""
        return self.peak_hours_used >= MAX_PEAK_HOURS * 60

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

        # Rule: Min duration
        if duration_minutes < MIN_BOOKING_MINUTES:
            return False, "Duration too short"

        # Rule: Max duration
        if duration_minutes > MAX_BOOKING_HOURS * 60:
            return False, "Duration too long"

        # Rule: Check conflict ranges
        if self.overlaps_conflict(date, start_hour, end_hour):
            return False, "Overlaps with your existing reservation"

        # Rule: Peak hours limit
        if date.weekday() < 5:  # Weekday
            overlap_start = max(start_hour, PEAK_START)
            overlap_end = min(end_hour, PEAK_END)
            if overlap_end > overlap_start:
                new_peak = (overlap_end - overlap_start) * 60
                if self.peak_hours_used + new_peak > MAX_PEAK_HOURS * 60:
                    return False, f"Peak hours limit ({self.peak_hours_used:.0f}/{MAX_PEAK_HOURS * 60} min used)"

        # Rule: Same room gap
        for b_room, b_date, b_start, b_end in self.bookings:
            if b_room == room and b_date.date() == date.date():
                gap = (start_hour - b_end) * 60
                if 0 < gap < SAME_ROOM_GAP_MINUTES:
                    return False, f"Need {SAME_ROOM_GAP_MINUTES}min gap (only {gap:.0f}min)"

        return True, ""

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
    booking_duration = min(slot_duration, MAX_BOOKING_HOURS * 60)
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

    # Check if room is available based on its booking horizon
    is_available, horizon_reason = is_room_available_to_book(room, target_date, start_hour)
    if not is_available:
        print(f"  Skipping {room} {book_start}: {horizon_reason}")
        return False

    print(f"\n{'='*60}")
    print(f"Trying: {room} at {book_start}-{book_end}")
    print(f"{'='*60}")

    # Step 1: Click on empty slot
    # Find the room row and scroll it into view, then get fresh coordinates
    room_name = slot['room']
    start_hour = slot['start_hour']
    end_hour = min(slot['end_hour'], start_hour + MAX_BOOKING_HOURS)

    # Calculate click percentage for the target time
    grid_start_hour = 7.0
    pct_per_hour = 6.25
    click_pct = ((start_hour + end_hour) / 2 - grid_start_hour) * pct_per_hour

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

        // Scroll row into view
        targetRow.scrollIntoView({{ behavior: 'instant', block: 'center' }});

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

        return {{ x: clickX, y: clickY }};
    }}""")

    if not coords:
        print(f"  [DEBUG] Could not find room '{room_name}' in grid")
        print(f"  Could not find room '{room_name}' in grid - skipping")
        return False

    page.wait_for_timeout(300)  # Brief wait after scroll

    # Log viewport info
    viewport = page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight, scrollY: window.scrollY })")
    print(f"  [DEBUG] Viewport: {viewport['width']}x{viewport['height']}, scrollY={viewport['scrollY']:.0f}")
    print(f"  [DEBUG] Click target: x={coords['x']:.0f}, y={coords['y']:.0f} (target time: {start_hour:.2f}-{end_hour:.2f})")

    # Check if click is within viewport
    if coords['y'] < 0 or coords['y'] > viewport['height']:
        print(f"  [DEBUG] WARNING: Click Y={coords['y']:.0f} is outside viewport (0-{viewport['height']})")

    print(f"  Clicking at ({coords['x']:.0f}, {coords['y']:.0f}) for {room_name}...")
    page.mouse.click(coords['x'], coords['y'])
    page.wait_for_timeout(2000)

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

    # Step 2: Click "Student booking provisional"
    print(f"  [DEBUG] Looking for 'Student booking provisional' button...")
    student_btn = page.locator("mat-list-item:has-text('Student booking provisional')").first
    if student_btn.count() > 0 and student_btn.is_visible():
        print("  Found 'Student booking provisional' button")
        student_btn.click()
        page.wait_for_timeout(3000)
    else:
        alt_btn = page.locator("text=Student booking provisional").first
        if alt_btn.count() > 0 and alt_btn.is_visible():
            print("  Found alt 'Student booking provisional' text")
            alt_btn.click()
            page.wait_for_timeout(3000)
        else:
            # Debug: Log what IS visible in any dialog
            dialog = page.locator("mat-dialog-container").first
            if dialog.count() > 0:
                dialog_text = dialog.text_content() or ""
                print(f"  [DEBUG] Dialog found, content: {dialog_text[:200]}...")
            else:
                # Check for any visible popup/menu/overlay
                overlays = page.locator(".cdk-overlay-pane, .mat-menu-panel, mat-bottom-sheet-container").all()
                visible_overlays = [o for o in overlays if o.is_visible()]
                if visible_overlays:
                    for i, overlay in enumerate(visible_overlays[:2]):
                        text = overlay.text_content() or ""
                        print(f"  [DEBUG] Overlay {i}: {text[:150]}...")
                else:
                    print("  [DEBUG] No dialog or overlay visible after click")

                # Log current page state
                current_url = page.url
                print(f"  [DEBUG] Current URL: {current_url[:60]}...")

            print("  No booking popup - skipping")
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
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

        # Scroll down
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(800)

        # Check if we've reached the bottom (no new content)
        new_height = page.evaluate("() => document.body.scrollHeight")
        if new_height == last_height:
            print(f"  [DEBUG] Reached end of agenda page after {scroll_count} scrolls (height: {new_height})")
            break
        last_height = new_height
        scroll_count += 1
        if scroll_count % 10 == 0:
            print(f"  [DEBUG] Still scrolling... ({scroll_count} scrolls, height: {new_height})")

    # Scroll back to top to ensure we capture everything
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)

    # Now scroll through the entire page once more to ensure all content is in DOM
    print("  Final pass to capture all events...")
    for _ in range(scroll_count + 5):
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        page.wait_for_timeout(300)

    page.wait_for_timeout(1000)

    # Extract events using JavaScript to get structured data
    # This version checks for cancelled events (strikethrough/red styling) and filters them out
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
                const textColor = style.color;
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

                const daysDiff = Math.floor((currentDate - today) / (1000 * 60 * 60 * 24));
                if (daysDiff >= 0 && daysDiff <= 7) {
                    events.push({
                        date: currentDate.toISOString().split('T')[0],
                        daysDiff: daysDiff,
                        startTime: startTime,
                        endTime: endTime,
                        cancelled: false
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

    events_found = 0
    for event in unique_events:
        date_str = event['date']
        start_time = event['startTime']
        end_time = event['endTime']
        days_diff = event['daysDiff']

        # Parse times to hours
        start_parts = start_time.split(':')
        end_parts = end_time.split(':')
        start_hour = int(start_parts[0]) + int(start_parts[1]) / 60
        end_hour = int(end_parts[0]) + int(end_parts[1]) / 60

        # Calculate the target date
        target_date = datetime.combine(today + timedelta(days=days_diff), datetime.min.time())

        # Add to conflict ranges for this date (also tracks peak hours)
        tracker.add_existing_event(target_date, start_hour, end_hour)
        events_found += 1

        day_name = target_date.strftime("%A")
        print(f"    {date_str} ({day_name}): {start_time} - {end_time}")

    print(f"\n  Total conflicts added: {events_found}")
    print(f"  Peak hours used from existing events: {tracker.peak_hours_used:.0f} min / {MAX_PEAK_HOURS * 60} min")
    print("="*60)
    return events_found


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
            # Format each booking nicely: "2026-02-04 B1.09 13:00" -> "Wed 4 Feb: B1.09 @ 13:00"
            formatted = []
            for detail in booking_details[:5]:
                parts = detail.split()
                if len(parts) >= 3:
                    try:
                        date_obj = datetime.strptime(parts[0], "%Y-%m-%d")
                        day_name = date_obj.strftime("%a")
                        day_num = date_obj.day
                        month = date_obj.strftime("%b")
                        room = parts[1]
                        time = parts[2]
                        formatted.append(f"{day_name} {day_num} {month}: {room} @ {time}")
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
    args = parser.parse_args()

    today = datetime.now().date()

    print("="*60)
    print("ASIMUT WEEK BOOKER")
    print("="*60)
    print(f"Today: {today}")
    print(f"Booking for: {today + timedelta(days=1)} to {today + timedelta(days=7)}")
    print(f"Mode: {'Headless' if args.headless else 'Visible browser'}")
    print("="*60)

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
        events_detected = scan_agenda(page, tracker, today)

        # Navigate to practice rooms
        print("\nNavigating to practice rooms...")
        page.goto("https://rwcmd.asimut.net/", wait_until="networkidle")
        page.wait_for_timeout(2000)

        for loc in page.locator("text=Locations").all():
            if loc.is_visible():
                loc.click()
                page.wait_for_timeout(2000)
                break

        for loc in page.locator("text=Music Practice Rooms - AHC").all():
            if loc.is_visible():
                loc.click()
                page.wait_for_timeout(3000)
                break

        # Navigate to day 1 first (tomorrow)
        print("\n  Starting navigation from today...")
        page.goto("https://rwcmd.asimut.net/overview?locationGroupId=10", wait_until="networkidle")
        page.wait_for_timeout(3000)

        # Log what date is currently showing
        current_header = page.locator("h1, h2, .title").first.text_content() if page.locator("h1, h2, .title").first.count() > 0 else "Unknown"
        print(f"  Currently showing: {current_header}")

        # Process each day
        for days_ahead in range(1, 8):
            target_date = datetime.combine(today + timedelta(days=days_ahead), datetime.min.time())
            day_name = target_date.strftime("%A")

            print(f"\n{'#'*60}")
            print(f"# DAY {days_ahead}: {target_date.strftime('%Y-%m-%d')} ({day_name})")
            print(f"{'#'*60}")
            print(f"  [DEBUG] Session stats: {total_booked} bookings so far, peak hours used: {tracker.peak_hours_used:.0f}min")

            # Navigate forward one day (just click right arrow once)
            print(f"  Navigating forward...")
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
                    if attempt == 0:
                        print(f"  [DEBUG] Current URL: {current_url[:60]}...")

                    # Find and click the right arrow - try several methods
                    arrow = None

                    # Method 1: mat-icon with chevron_right text
                    try:
                        arrow = page.locator("mat-icon").filter(has_text="chevron_right").first
                        arrow_count = arrow.count()
                        arrow_visible = arrow.is_visible() if arrow_count > 0 else False
                        if arrow_count > 0 and arrow_visible:
                            arrow.click(force=True)
                            page.wait_for_timeout(2000)
                            clicked = True
                            break
                    except Exception as e:
                        if attempt == 0:
                            print(f"  [DEBUG] Method 1 failed: {e}")

                    # Method 2: Button containing chevron_right
                    try:
                        arrow = page.locator("button:has(mat-icon:text('chevron_right'))").first
                        if arrow.count() > 0 and arrow.is_visible():
                            arrow.click(force=True)
                            page.wait_for_timeout(2000)
                            clicked = True
                            break
                    except:
                        pass

                    # Method 3: Any element with chevron_right
                    try:
                        arrow = page.locator("text=chevron_right").first
                        if arrow.count() > 0 and arrow.is_visible():
                            arrow.click(force=True)
                            page.wait_for_timeout(2000)
                            clicked = True
                            break
                    except:
                        pass

                    # After 2 failed attempts, try reloading the page
                    if attempt == 2:
                        print(f"  [DEBUG] Reloading calendar page after failed navigation...")
                        page.goto("https://rwcmd.asimut.net/overview?locationGroupId=10", wait_until="networkidle")
                        page.wait_for_timeout(2000)
                        # Click forward the correct number of times
                        print(f"  [DEBUG] Clicking forward {days_ahead} times...")
                        for click_num in range(days_ahead):
                            try:
                                page.evaluate("window.scrollTo(0, 0)")
                                page.wait_for_timeout(300)
                                arrow = page.locator("mat-icon").filter(has_text="chevron_right").first
                                if arrow.count() > 0:
                                    arrow.click(force=True)
                                    page.wait_for_timeout(1500)
                            except Exception as e:
                                print(f"  [DEBUG] Forward click {click_num + 1} failed: {e}")
                        clicked = True
                        break

                    print(f"    Arrow not found (attempt {attempt + 1}), waiting...")
                    page.wait_for_timeout(1500)
                except Exception as e:
                    print(f"    Arrow click failed: {e}")
                    page.wait_for_timeout(1000)

            if not clicked:
                print(f"  [DEBUG] Navigation failed after all attempts")
                print(f"  Failed to navigate forward, skipping...")
                continue

            print(f"  [DEBUG] Navigation succeeded, waiting for page to stabilize...")
            page.wait_for_timeout(1000)

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

            # Sort: good slots first (longer), then by time, then by room priority
            all_slots.sort(key=lambda s: (not s['is_good'], s['start_hour'], s['room_priority']))

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

            # Log peak quota status
            remaining_peak = tracker.get_remaining_peak_minutes()
            print(f"  [DEBUG] Peak quota: {tracker.peak_hours_used:.0f}/{MAX_PEAK_HOURS * 60} min used, {remaining_peak:.0f} min remaining")

            # Adjust slots to avoid conflicts AND peak zone if quota exceeded
            adjusted_slots = []
            conflicts = tracker.conflict_ranges.get(target_date.strftime('%Y-%m-%d'), [])

            if conflicts:
                print(f"  [DEBUG] Known conflicts for this day: {[(f'{c[0]:.2f}-{c[1]:.2f}') for c in conflicts]}")

            slots_removed = 0
            slots_adjusted = 0
            peak_adjusted = 0
            conflict_removed = 0
            for s in all_slots:
                slot_start = s['start_hour']
                slot_end = s['end_hour']
                original_start = slot_start
                original_end = slot_end

                # Step 1: Check if slot overlaps with any conflict
                for c_start, c_end in conflicts:
                    if slot_start < c_end and slot_end > c_start:
                        # Slot overlaps with conflict - try to use the part after the conflict
                        if c_end < slot_end:
                            # There's usable time after the conflict
                            slot_start = c_end
                        else:
                            # Entire slot is within conflict
                            slot_start = slot_end  # Will be filtered out below
                            break

                # Skip if slot was fully consumed by conflict
                if slot_start >= slot_end or (slot_end - slot_start) * 60 < MIN_BOOKING_MINUTES:
                    conflict_removed += 1
                    continue

                # Step 2: Adjust for peak hours quota (only on weekdays)
                adj_start, adj_end, was_adjusted, reason = adjust_slot_for_peak_quota(
                    slot_start, slot_end, target_date, remaining_peak
                )

                if adj_start is None:
                    # Slot should be skipped (fully in peak zone with no quota)
                    print(f"  [DEBUG] Skipping {s['room']} {slot_start:.2f}-{slot_end:.2f}: {reason}")
                    slots_removed += 1
                    continue

                if was_adjusted:
                    slot_start = adj_start
                    slot_end = adj_end
                    peak_adjusted += 1
                    print(f"  [DEBUG] Peak-adjusted {s['room']}: {original_start:.2f}-{original_end:.2f} -> {slot_start:.2f}-{slot_end:.2f}")

                # Final validation
                if slot_start < slot_end and (slot_end - slot_start) * 60 >= MIN_BOOKING_MINUTES:
                    s = s.copy()
                    s['start_hour'] = slot_start
                    s['duration'] = slot_end - slot_start
                    s['end_hour'] = slot_end
                    adjusted_slots.append(s)
                    if slot_start != original_start or slot_end != original_end:
                        slots_adjusted += 1
                else:
                    slots_removed += 1

            if conflict_removed > 0 or slots_removed > 0 or slots_adjusted > 0 or peak_adjusted > 0:
                print(f"  [DEBUG] Slot adjustment: {conflict_removed} conflict-removed, {slots_removed} peak-removed, {slots_adjusted} adjusted, {peak_adjusted} peak-adjusted")

            all_slots = adjusted_slots
            all_slots.sort(key=lambda s: (not s.get('is_good', False), s['start_hour'], s['room_priority']))

            print(f"  [DEBUG] Starting booking attempts. Max per day: {MAX_BOOKINGS_PER_DAY}, slots available: {len(all_slots)}")

            while day_booked < MAX_BOOKINGS_PER_DAY and attempts < max_attempts:
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

                    # Record booking detail
                    room_name = slot['room']
                    start_h = int(slot['start_hour'])
                    start_m = int((slot['start_hour'] % 1) * 60)
                    booking_details.append(f"{target_date.strftime('%Y-%m-%d')} {room_name} {start_h:02d}:{start_m:02d}")

                    # After successful booking, refresh slot coordinates
                    page.wait_for_timeout(2000)

                    # Re-fetch available slots since the calendar has changed
                    print("  [DEBUG] Refreshing slot data after successful booking...")
                    available_data = get_available_slots(page)

                    # Recalculate remaining peak quota after booking
                    remaining_peak = tracker.get_remaining_peak_minutes()
                    print(f"  [DEBUG] Updated peak quota: {remaining_peak:.0f} min remaining")

                    # Rebuild slot list with peak adjustment
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

                            # Apply peak adjustment
                            adj_start, adj_end, was_adjusted, reason = adjust_slot_for_peak_quota(
                                slot_start, slot_end, target_date, remaining_peak
                            )

                            if adj_start is None:
                                continue  # Skip slots fully in peak zone with no quota

                            if was_adjusted:
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

                    all_slots.sort(key=lambda s: (not s['is_good'], s['start_hour'], s['room_priority']))
                    print(f"  [DEBUG] Rebuilt slot list: {len(all_slots)} slots remaining")

            print(f"\n  Day summary: {day_booked} bookings made, {attempts} attempts total")

        # Final summary
        print("\n" + "="*60)
        print("BOOKING COMPLETE")
        print("="*60)
        print(f"Total bookings made: {total_booked}")
        print(f"Peak hours used: {tracker.peak_hours_used:.0f} minutes")
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
