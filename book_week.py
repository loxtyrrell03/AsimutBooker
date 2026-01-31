"""Book practice rooms for the entire week (7 days ahead).

This script applies all RWCMD booking rules:
- Rolling quota of 28 bookings
- Maximum 2hr peak allowance (Mon-Fri 9am-4pm)
- Minimum 30 min, maximum 2hr per booking
- 60-minute gap between same room bookings

Usage:
    python book_week.py              # Run with visible browser
    python book_week.py --headless   # Run without browser window (for scheduled tasks)
"""

import re
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

state_file = Path("data/browser_state/state.json")

# Booking rules
MAX_BOOKING_HOURS = 2
MIN_BOOKING_MINUTES = 30
PREFERRED_MIN_MINUTES = 60  # Prefer slots at least 1 hour
PEAK_START = 9   # 9am
PEAK_END = 16    # 4pm
MAX_PEAK_HOURS = 2
SAME_ROOM_GAP_MINUTES = 60
MAX_BOOKINGS_PER_DAY = 3  # Limit per day to spread across week

# Priority rooms in order
PRIORITY_ROOMS = [
    "B0.29", "B1.09", "B0.11", "B1.16", "B0.15", "B0.13", "B0.27",
    "B1.14", "B1.17", "B1.20", "B1.18", "B1.21", "B1.19",
    "B1.06", "B1.07", "B1.08", "B1.10", "B1.11"
]


class BookingTracker:
    """Tracks bookings and enforces rules."""

    def __init__(self):
        self.bookings = []  # List of (room, date, start_hour, end_hour)
        self.conflict_ranges = {}  # Per-day conflict ranges: {date: [(start, end), ...]}
        self.peak_hours_used = 0  # Total minutes of peak time used

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
        """Record a discovered conflict range."""
        date_key = date.strftime('%Y-%m-%d')
        if date_key not in self.conflict_ranges:
            self.conflict_ranges[date_key] = []
        self.conflict_ranges[date_key].append((start_hour, end_hour))
        print(f"  [Tracker] Added conflict: {date_key} {start_hour:.2f}-{end_hour:.2f}")

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
                        gaps.push({ startHour, endHour, clickX, clickY: midY });
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
                    gaps.push({ startHour, endHour, clickX, clickY: midY });
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

    print(f"\n{'='*60}")
    print(f"Trying: {room} at {book_start}-{book_end}")
    print(f"{'='*60}")

    # Step 1: Click on empty slot
    print("  Clicking on slot...")
    page.mouse.click(slot["click_x"], slot["click_y"])
    page.wait_for_timeout(2000)

    # Check if we accidentally clicked on an existing reservation
    # The unique indicator is "Events where I participate" or "Choose which events to display"
    events_participate = page.locator("text=Events where I participate").first
    choose_events = page.locator("text=Choose which events to display").first

    if (events_participate.count() > 0 and events_participate.is_visible()) or \
       (choose_events.count() > 0 and choose_events.is_visible()):
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
    student_btn = page.locator("mat-list-item:has-text('Student booking provisional')").first
    if student_btn.count() > 0 and student_btn.is_visible():
        student_btn.click()
        page.wait_for_timeout(3000)
    else:
        alt_btn = page.locator("text=Student booking provisional").first
        if alt_btn.count() > 0 and alt_btn.is_visible():
            alt_btn.click()
            page.wait_for_timeout(3000)
        else:
            print("  No booking popup - skipping")
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)
            return False

    # Step 3: Set times
    start_input = page.locator("#startDate")
    end_input = page.locator("#endDate")

    if start_input.count() > 0 and end_input.count() > 0:
        start_input.click()
        start_input.fill(book_start)
        page.wait_for_timeout(300)

        end_input.click()
        end_input.fill(book_end)
        page.wait_for_timeout(300)

        # Dismiss dropdown
        save_btn = page.locator("button:has-text('Save')").first
        if save_btn.count() > 0:
            box = save_btn.bounding_box()
            if box:
                page.mouse.click(box['x'] + 10, box['y'] - 20)
        page.wait_for_timeout(500)

    # Step 4: Check for errors - look for ANY conflict indicator
    # First, search for any reservation time patterns on the page (your existing bookings)
    all_reservation_times = page.locator("text=/\\d{2}:\\d{2}\\s*-\\s*\\d{2}:\\d{2}/").all()
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
        except:
            pass

    error_msg = page.locator("text=You are not allowed").first
    if error_msg.count() > 0 and error_msg.is_visible():
        print("  Error: Not allowed to book")

        # Extract conflict time
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
    save_btn = page.locator("button:has-text('Save')").first
    if save_btn.count() > 0 and save_btn.is_visible():
        # Check if enabled
        save_icon = save_btn.locator("mat-icon").first
        if save_icon.count() > 0:
            icon_text = save_icon.text_content() or ""
            if icon_text in ["remove", "block", "close"]:
                print(f"  Save disabled (icon: {icon_text})")
                go_back(page, days_ahead)
                return False

        print("  Clicking Save...")
        save_btn.click()
        page.wait_for_timeout(3000)

        # Check for post-save errors first
        post_conflict = page.locator("text=conflicting events").first
        post_not_allowed = page.locator("text=not allowed").first

        if post_conflict.count() > 0 and post_conflict.is_visible():
            print("  Save failed - conflict after save")
            go_back(page, days_ahead)
            return False

        if post_not_allowed.count() > 0 and post_not_allowed.is_visible():
            print("  Save failed - not allowed after save")
            go_back(page, days_ahead)
            return False

        # Check success - booking form should be gone
        booking_form = page.locator("#startDate")
        if booking_form.count() == 0 or not booking_form.is_visible():
            # Success!
            tracker.add_booking(room, target_date, start_hour, end_hour)
            print(f"  SUCCESS: Booked {room} {book_start}-{book_end}")
            return True
        else:
            # Check if there's an actual error or just delay
            page.wait_for_timeout(1000)
            booking_form = page.locator("#startDate")
            if booking_form.count() == 0 or not booking_form.is_visible():
                # Success after extra wait
                tracker.add_booking(room, target_date, start_hour, end_hour)
                print(f"  SUCCESS: Booked {room} {book_start}-{book_end}")
                return True

            print("  Save may have failed - still on form")
            go_back(page, days_ahead)
            return False
    else:
        print("  Save button not found")
        go_back(page, days_ahead)
        return False


def go_back(page, days_ahead=None):
    """Navigate back to calendar by clicking the back button."""
    # Click the back button with aria-label="Back to previous page"
    back_btn = page.locator("button[aria-label='Back to previous page']").first
    if back_btn.count() > 0 and back_btn.is_visible():
        back_btn.click()
        page.wait_for_timeout(2000)
    else:
        # Fallback: use browser back
        page.go_back()
        page.wait_for_timeout(2000)


def scan_agenda(page, tracker, today):
    """Scan the My Agenda page to find all existing events and reservations for the next 7 days."""
    print("\n" + "="*60)
    print("SCANNING MY AGENDA FOR EXISTING EVENTS")
    print("="*60)

    # Navigate directly to the agenda page
    page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
    page.wait_for_timeout(3000)

    # Parse all events from the agenda page
    # The agenda shows events grouped by date with time ranges
    events_found = 0

    # Get all event entries on the page
    # Look for time patterns like "09:30 - 11:00" or "11:00 - 13:00"
    page_content = page.content()

    # Parse events by looking at the agenda structure
    # Each event has a time range and details
    event_items = page.locator(".event-list-item, [class*='event'], mat-list-item").all()

    # Also look for specific date headers and their events
    # The page shows dates like "Sunday 1 February 2026" followed by events

    # Extract events using JavaScript to get structured data
    events_data = page.evaluate("""() => {
        const events = [];
        const today = new Date();
        today.setHours(0, 0, 0, 0);

        // Find all date sections and their events
        const content = document.body.innerText;
        const lines = content.split('\\n');

        let currentDate = null;
        let currentYear = today.getFullYear();
        let currentMonth = today.getMonth();

        const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                          'July', 'August', 'September', 'October', 'November', 'December'];

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trim();

            // Check for date headers like "Sunday 1 February 2026" or "Saturday 31 January 2026"
            const dateMatch = line.match(/(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\\s+(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)\\s+(\\d{4})/i);
            if (dateMatch) {
                const day = parseInt(dateMatch[2]);
                const month = monthNames.indexOf(dateMatch[3]);
                const year = parseInt(dateMatch[4]);
                currentDate = new Date(year, month, day);
                continue;
            }

            // Check for time ranges like "09:30 - 11:00" or "11:00 - 13:00"
            const timeMatch = line.match(/^(\\d{2}:\\d{2})\\s*[-–]\\s*(\\d{2}:\\d{2})$/);
            if (timeMatch && currentDate) {
                const startTime = timeMatch[1];
                const endTime = timeMatch[2];

                // Only include events within the next 7 days
                const daysDiff = Math.floor((currentDate - today) / (1000 * 60 * 60 * 24));
                if (daysDiff >= 0 && daysDiff <= 7) {
                    events.push({
                        date: currentDate.toISOString().split('T')[0],
                        daysDiff: daysDiff,
                        startTime: startTime,
                        endTime: endTime
                    });
                }
            }
        }

        return events;
    }""")

    print(f"\n  Found {len(events_data)} events in agenda:")

    for event in events_data:
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

        # Add to conflict ranges for this date
        tracker.add_conflict(target_date, start_hour, end_hour)
        events_found += 1

        day_name = target_date.strftime("%A")
        print(f"    {date_str} ({day_name}): {start_time} - {end_time}")

    print(f"\n  Total conflicts added: {events_found}")
    print("="*60)


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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(
            storage_state=str(state_file),
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        # First, scan the agenda to learn about existing events
        scan_agenda(page, tracker, today)

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

        # Process each day
        for days_ahead in range(1, 8):
            target_date = datetime.combine(today + timedelta(days=days_ahead), datetime.min.time())
            day_name = target_date.strftime("%A")

            print(f"\n{'#'*60}")
            print(f"# DAY {days_ahead}: {target_date.strftime('%Y-%m-%d')} ({day_name})")
            print(f"{'#'*60}")

            # Navigate to target day from today's view
            # Always start fresh from the overview to ensure we're on the right day
            print(f"  Navigating to day {days_ahead}...")
            page.goto("https://rwcmd.asimut.net/overview?locationGroupId=10", wait_until="networkidle")
            page.wait_for_timeout(2000)

            # Click forward arrow days_ahead times
            nav_success = True
            for day_click in range(days_ahead):
                clicked = False
                for attempt in range(3):
                    try:
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)

                        arrow = page.locator("mat-icon:text('chevron_right')").first
                        if arrow.count() > 0 and arrow.is_visible():
                            arrow.click(force=True, timeout=5000)
                            page.wait_for_timeout(1000)
                            clicked = True
                            break
                    except Exception as e:
                        print(f"    Arrow click failed: {e}")
                        page.wait_for_timeout(500)

                if not clicked:
                    print(f"  Failed to navigate forward (day {day_click + 1})")
                    nav_success = False
                    break

            if not nav_success:
                print(f"  Failed to navigate to day {days_ahead}, skipping...")
                continue

            page.wait_for_timeout(1000)

            # Get available slots
            available_data = get_available_slots(page)

            # Build sorted slot list - prefer longer slots
            all_slots = []
            for room_data in available_data:
                room_name = room_data["room"]
                if room_name not in PRIORITY_ROOMS:
                    continue

                room_priority = PRIORITY_ROOMS.index(room_name)

                for slot in room_data["slots"]:
                    slot_duration = slot['endHour'] - slot['startHour']
                    if slot_duration * 60 < MIN_BOOKING_MINUTES:
                        continue  # Too short

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

            # Sort: good slots first (longer), then by time, then by room priority
            all_slots.sort(key=lambda s: (not s['is_good'], s['start_hour'], s['room_priority']))

            if not all_slots:
                print("  No available slots found")
                continue

            print(f"  Found {len(all_slots)} potential slots")

            # Try to book slots
            day_booked = 0
            attempts = 0
            max_attempts = 15  # Reduce to avoid too many failed attempts

            while day_booked < MAX_BOOKINGS_PER_DAY and attempts < max_attempts:
                # Check if we have valid slots to try
                valid_slots = [s for s in all_slots if not tracker.overlaps_conflict(
                    target_date, s['start_hour'], s['start_hour'] + min(s['duration'], MAX_BOOKING_HOURS)
                )]

                if not valid_slots:
                    print("  No more valid slots available")
                    break

                slot = valid_slots[0]  # Try the best remaining slot
                all_slots.remove(slot)  # Remove from list so we don't retry
                attempts += 1

                if try_book_slot(page, slot, target_date, tracker, days_ahead):
                    day_booked += 1
                    total_booked += 1

                    # After successful booking, refresh slot coordinates
                    page.wait_for_timeout(2000)

                    # Re-fetch available slots since the calendar has changed
                    print("  Refreshing slot data...")
                    available_data = get_available_slots(page)

                    # Rebuild slot list
                    all_slots = []
                    for room_data in available_data:
                        room_name = room_data["room"]
                        if room_name not in PRIORITY_ROOMS:
                            continue

                        room_priority = PRIORITY_ROOMS.index(room_name)

                        for slot_data in room_data["slots"]:
                            slot_duration = slot_data['endHour'] - slot_data['startHour']
                            if slot_duration * 60 < MIN_BOOKING_MINUTES:
                                continue

                            is_good_slot = slot_duration >= 1.0

                            all_slots.append({
                                "room": room_name,
                                "start_hour": slot_data['startHour'],
                                "end_hour": slot_data['endHour'],
                                "duration": slot_duration,
                                "room_priority": room_priority,
                                "is_good": is_good_slot,
                                "click_x": slot_data["clickX"],
                                "click_y": slot_data["clickY"]
                            })

                    all_slots.sort(key=lambda s: (not s['is_good'], s['start_hour'], s['room_priority']))

            print(f"\n  Day summary: {day_booked} bookings made")

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

        if not args.headless:
            print("\nKeeping browser open for 30 seconds to verify...")
            page.wait_for_timeout(30000)

        context.close()
        browser.close()


if __name__ == "__main__":
    main()
