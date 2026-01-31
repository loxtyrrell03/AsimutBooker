"""Core booking logic for Asimut practice rooms."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from playwright.sync_api import Page, Locator, TimeoutError as PlaywrightTimeout

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class ExistingBooking:
    """Represents an existing user booking for rule tracking."""
    room: str
    date: datetime
    start_time: str  # HH:MM format
    end_time: str    # HH:MM format

    def start_hour(self) -> float:
        """Get start time as decimal hour."""
        h, m = map(int, self.start_time.split(":"))
        return h + m / 60

    def end_hour(self) -> float:
        """Get end time as decimal hour."""
        h, m = map(int, self.end_time.split(":"))
        return h + m / 60

    def duration_minutes(self) -> float:
        """Get duration in minutes."""
        return (self.end_hour() - self.start_hour()) * 60

    def is_peak_hours(self, peak_start: str, peak_end: str, peak_days: list[int]) -> bool:
        """Check if this booking overlaps with peak hours."""
        # Check if the day is a peak day (weekday)
        if self.date.weekday() not in peak_days:
            return False

        peak_start_h = int(peak_start.split(":")[0]) + int(peak_start.split(":")[1]) / 60
        peak_end_h = int(peak_end.split(":")[0]) + int(peak_end.split(":")[1]) / 60

        # Check for overlap
        return self.start_hour() < peak_end_h and self.end_hour() > peak_start_h

    def peak_hours_overlap(self, peak_start: str, peak_end: str, peak_days: list[int]) -> float:
        """Calculate how many minutes of this booking overlap with peak hours."""
        if self.date.weekday() not in peak_days:
            return 0

        peak_start_h = int(peak_start.split(":")[0]) + int(peak_start.split(":")[1]) / 60
        peak_end_h = int(peak_end.split(":")[0]) + int(peak_end.split(":")[1]) / 60

        # Calculate overlap
        overlap_start = max(self.start_hour(), peak_start_h)
        overlap_end = min(self.end_hour(), peak_end_h)

        if overlap_end > overlap_start:
            return (overlap_end - overlap_start) * 60
        return 0


class RulesEnforcer:
    """Enforces RWCMD booking rules."""

    def __init__(self, config: "Config"):
        self.config = config
        self.existing_bookings: list[ExistingBooking] = []
        self.conflict_ranges: list[tuple[float, float]] = []  # (start_hour, end_hour) tuples

    def add_existing_booking(self, booking: ExistingBooking) -> None:
        """Add an existing booking for tracking."""
        self.existing_bookings.append(booking)
        logger.debug("Tracking existing booking: %s %s %s-%s",
                    booking.room, booking.date.strftime("%Y-%m-%d"),
                    booking.start_time, booking.end_time)

    def add_conflict_range(self, start_hour: float, end_hour: float) -> None:
        """Add a conflict range discovered during booking attempts."""
        self.conflict_ranges.append((start_hour, end_hour))
        logger.info("Added conflict range: %.2f - %.2f", start_hour, end_hour)

    def get_total_bookings(self) -> int:
        """Get total number of active bookings."""
        return len(self.existing_bookings)

    def get_peak_hours_used(self) -> float:
        """Get total peak hours used in minutes."""
        total_minutes = 0
        for booking in self.existing_bookings:
            total_minutes += booking.peak_hours_overlap(
                self.config.peak_hours_start,
                self.config.peak_hours_end,
                self.config.peak_hours_days
            )
        return total_minutes

    def overlaps_conflict(self, start_hour: float, end_hour: float) -> bool:
        """Check if a time range overlaps with any known conflict."""
        for c_start, c_end in self.conflict_ranges:
            if start_hour < c_end and end_hour > c_start:
                return True
        return False

    def can_book_slot(self, slot: "TimeSlot", booking_duration_minutes: int) -> tuple[bool, str]:
        """
        Check if a slot can be booked according to all rules.

        Returns:
            Tuple of (can_book, reason_if_not)
        """
        # Rule 1: Check rolling quota
        if self.get_total_bookings() >= self.config.rolling_quota:
            return False, f"Rolling quota exceeded ({self.config.rolling_quota} bookings)"

        # Rule 2: Check duration limits
        if booking_duration_minutes < self.config.min_duration_minutes:
            return False, f"Duration too short (min {self.config.min_duration_minutes} min)"
        if booking_duration_minutes > self.config.max_duration_minutes:
            return False, f"Duration too long (max {self.config.max_duration_minutes} min)"

        # Rule 3: Check peak hours limit
        if self.config.peak_hours_enabled and slot.date:
            start_h = int(slot.start_time.split(":")[0]) + int(slot.start_time.split(":")[1]) / 60
            end_h = start_h + booking_duration_minutes / 60

            if slot.date.weekday() in self.config.peak_hours_days:
                peak_start_h = int(self.config.peak_hours_start.split(":")[0])
                peak_end_h = int(self.config.peak_hours_end.split(":")[0])

                # Calculate peak overlap for this potential booking
                overlap_start = max(start_h, peak_start_h)
                overlap_end = min(end_h, peak_end_h)
                if overlap_end > overlap_start:
                    new_peak_minutes = (overlap_end - overlap_start) * 60
                    current_peak_used = self.get_peak_hours_used()
                    max_peak_minutes = self.config.peak_hours_max * 60

                    if current_peak_used + new_peak_minutes > max_peak_minutes:
                        return False, f"Peak hours limit exceeded ({current_peak_used:.0f}/{max_peak_minutes:.0f} min used)"

        # Rule 4: Check same-room gap
        gap_required_minutes = self.config.same_room_gap_minutes
        slot_start_h = int(slot.start_time.split(":")[0]) + int(slot.start_time.split(":")[1]) / 60

        for existing in self.existing_bookings:
            if existing.room == slot.room and existing.date.date() == (slot.date.date() if slot.date else None):
                # Same room, same day - check gap
                gap_minutes = (slot_start_h - existing.end_hour()) * 60
                if 0 < gap_minutes < gap_required_minutes:
                    return False, f"Same-room gap too short ({gap_minutes:.0f} min, need {gap_required_minutes})"

        # Rule 5: Check for known conflicts (from previous booking attempts)
        slot_end_h = slot_start_h + booking_duration_minutes / 60
        if self.overlaps_conflict(slot_start_h, slot_end_h):
            return False, "Overlaps with known conflict range"

        return True, ""

    def calculate_valid_duration(self, slot: "TimeSlot") -> int:
        """
        Calculate the valid booking duration for a slot, respecting all rules.

        Returns:
            Duration in minutes (0 if slot cannot be booked)
        """
        # Parse slot times
        start_parts = slot.start_time.split(":")
        end_parts = slot.end_time.split(":")
        start_h = int(start_parts[0]) + int(start_parts[1]) / 60
        end_h = int(end_parts[0]) + int(end_parts[1]) / 60

        # Available slot duration
        slot_duration = (end_h - start_h) * 60

        # Cap at max duration
        duration = min(slot_duration, self.config.max_duration_minutes)

        # Check if it meets minimum
        if duration < self.config.min_duration_minutes:
            return 0

        return int(duration)


@dataclass
class TimeSlot:
    """Represents a bookable time slot."""

    room: str
    date: datetime
    start_time: str
    end_time: str
    available: bool
    # Coordinates for clicking to book (center of the available gap)
    click_x: float | None = None
    click_y: float | None = None


@dataclass
class BookingResult:
    """Result of a booking attempt."""

    success: bool
    room: str | None = None
    date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    message: str = ""


class AsimutBooker:
    """Handles the booking logic for Asimut practice rooms."""

    # Available slots are white/transparent - anything with a visible color is unavailable

    def __init__(self, page: Page, config: "Config"):
        """
        Initialize the booker.

        Args:
            page: Playwright page object (already logged in)
            config: Configuration object
        """
        self.page = page
        self.config = config
        self.base_url = config.asimut_url.rstrip("/")
        self.rules = RulesEnforcer(config)

    def navigate_to_location(self) -> bool:
        """
        Navigate to the configured location (e.g., Music Practice Rooms - AHC).

        Returns:
            True if navigation successful, False otherwise.
        """
        try:
            logger.info("Navigating to location: %s", self.config.location)

            # Go to main page first
            self.page.goto(self.base_url, wait_until="networkidle", timeout=30000)
            self.page.wait_for_timeout(2000)

            # Click on "Locations" in the sidebar first
            logger.debug("Clicking on Locations...")
            locations_links = self.page.locator("text=Locations").all()
            for loc in locations_links:
                if loc.is_visible():
                    loc.click()
                    self.page.wait_for_timeout(2000)
                    break

            # Now click on the specific location (e.g., "Music Practice Rooms - AHC")
            logger.debug("Clicking on %s...", self.config.location)
            location_links = self.page.locator(f"text={self.config.location}").all()
            for loc in location_links:
                if loc.is_visible():
                    loc.click()
                    self.page.wait_for_timeout(3000)
                    logger.info("Successfully navigated to %s", self.config.location)
                    return True

            logger.error("Could not find location: %s", self.config.location)
            return False

        except PlaywrightTimeout:
            logger.error("Timeout navigating to location")
            return False
        except Exception as e:
            logger.error("Error navigating to location: %s", e)
            return False

    def navigate_to_date(self, target_date: datetime) -> bool:
        """
        Navigate to a specific date in the calendar view.

        Args:
            target_date: The date to navigate to.

        Returns:
            True if navigation successful, False otherwise.
        """
        try:
            logger.info("Navigating to date: %s", target_date.strftime("%Y-%m-%d"))

            # Get current date from the header
            current_date = self._get_current_calendar_date()

            if current_date is None:
                logger.error("Could not determine current calendar date")
                return False

            # Calculate how many days to navigate
            days_diff = (target_date.date() - current_date.date()).days

            if days_diff == 0:
                logger.info("Already on target date")
                return True

            logger.info("Need to navigate %d days %s", abs(days_diff),
                       "forward" if days_diff > 0 else "backward")

            # Find the navigation arrows
            for _ in range(abs(days_diff)):
                if days_diff > 0:
                    # Click forward arrow
                    arrow = self.page.locator("mat-icon:text('chevron_right'), mat-icon:text('keyboard_arrow_right')").first
                    if not (arrow.count() > 0 and arrow.is_visible()):
                        arrow = self.page.locator("button:has(mat-icon:text('chevron_right'))").first
                    if not (arrow.count() > 0 and arrow.is_visible()):
                        arrow = self.page.locator("text='>'").first
                else:
                    # Click backward arrow
                    arrow = self.page.locator("mat-icon:text('chevron_left'), mat-icon:text('keyboard_arrow_left')").first
                    if not (arrow.count() > 0 and arrow.is_visible()):
                        arrow = self.page.locator("button:has(mat-icon:text('chevron_left'))").first
                    if not (arrow.count() > 0 and arrow.is_visible()):
                        arrow = self.page.locator("text='<'").first

                if arrow.count() > 0 and arrow.is_visible():
                    arrow.click()
                    self.page.wait_for_timeout(1000)
                else:
                    logger.warning("Could not find navigation arrow")
                    break

            # Verify we're on the right date
            new_date = self._get_current_calendar_date()
            if new_date:
                logger.info("Now on date: %s", new_date.strftime("%Y-%m-%d"))

            return True

        except Exception as e:
            logger.error("Error navigating to date: %s", e)
            return False

    def _get_current_calendar_date(self) -> datetime | None:
        """Get the currently displayed date from the calendar header."""
        try:
            page_text = self.page.locator("body").text_content() or ""

            # Pattern: "Day, DD Month YYYY"
            pattern = r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})"
            match = re.search(pattern, page_text)

            if match:
                date_str = f"{match.group(2)} {match.group(3)} {match.group(4)}"
                return datetime.strptime(date_str, "%d %B %Y")

            return None

        except Exception as e:
            logger.debug("Error getting current date: %s", e)
            return None

    def get_available_slots(self) -> list[TimeSlot]:
        """
        Get all available time slots on the current page using JavaScript evaluation.

        The grid structure:
        - Room names are in .location-name-container .location-row
        - Events (bookings/blocks) are in .location-event elements
        - Events are positioned by Y coordinate to match room rows
        - Available slots are white cells: no text AND no colored background
        - Blue = confirmed booking, Grey = pending booking, Red = blocked - all unavailable

        Returns:
            List of available TimeSlot objects.
        """
        current_date = self._get_current_calendar_date() or datetime.now()

        # Find available slots by parsing CSS percentages for precise time calculations
        # Dynamically calculate grid mapping from time headers on the page
        available_data = self.page.evaluate("""() => {
            const rows = document.querySelectorAll('.location-name-container .location-row');
            const locationDays = document.querySelectorAll('.location-day');

            // Helper to parse percentage from style
            function parseStylePct(style, prop) {
                const match = style.match(new RegExp(prop + ':\\\\s*calc\\\\(([\\\\d.]+)%'));
                return match ? parseFloat(match[1]) : null;
            }

            // Grid mapping: 6.25% = 1 hour is reliable
            // The grid start time is determined by when the first closed block ends
            // Since time headers are not perfectly aligned, we use the closed blocks
            const pctPerHour = 6.25;

            // Find the opening time from the first room's closed block
            // The first closed block (at 0%) ends when rooms open
            let gridStartHour = 7.0;  // 0% = 07:00 (so 3.125% = 07:30 opening)

            // Try to detect from first closed area - if it starts at 0% and has width ~3.125%,
            // that means rooms are closed until 07:30
            if (locationDays.length > 0) {
                const firstDay = locationDays[0];
                const firstClosed = firstDay.querySelector('.location-closed');
                if (firstClosed) {
                    const style = firstClosed.getAttribute('style') || '';
                    const leftMatch = style.match(/left:\\s*calc\\(([\\d.]+)%/);
                    const widthMatch = style.match(/width:\\s*calc\\(([\\d.]+)%/);
                    const leftPct = leftMatch ? parseFloat(leftMatch[1]) : 0;
                    const widthPct = widthMatch ? parseFloat(widthMatch[1]) : 0;

                    // If closed block starts at 0%, use its width to find opening time
                    if (leftPct < 1) {
                        // widthPct / pctPerHour = hours of closure before opening
                        // Opening time = gridStartHour + (widthPct / pctPerHour)
                        // We want opening time = gridStartHour + width_hours
                        // Standard: 0% = 07:00, 3.125% width = 0.5 hours, so opens at 07:30
                        gridStartHour = 7.0;
                    }
                }
            }

            // Convert percentage to hour
            function pctToHour(pct) {
                return gridStartHour + (pct / pctPerHour);
            }

            // Convert hour to percentage
            function hourToPct(hour) {
                return (hour - gridStartHour) * pctPerHour;
            }

            const roomData = Array.from(rows).map((row, rowIdx) => {
                const rect = row.getBoundingClientRect();
                const roomName = row.textContent.trim();
                const midY = (rect.top + rect.bottom) / 2;

                // Find the corresponding location-day container by Y position
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

                // Get all blocked time ranges from events (parse from CSS, not pixels)
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

                // Also get closed areas
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

                // Combine and sort all blocked ranges
                const allBlocked = [...blockedRanges, ...closedRanges]
                    .sort((a, b) => a.startHour - b.startHour);

                // Find gaps between blocked ranges (working in hours)
                const gaps = [];
                let lastEndHour = 7.2;  // Grid starts at ~07:12

                // Find end of day from closed areas (typically the last closed area)
                let dayEndHour = 22.25;  // Default to 22:15
                closedRanges.forEach(r => {
                    if (r.startHour > 20) {  // Evening closing
                        dayEndHour = r.startHour;
                    }
                });

                allBlocked.forEach(range => {
                    if (range.startHour > lastEndHour + 0.25) {  // Gap of at least 15 min
                        // Round to nearest 15 minutes, with small tolerance for floating point
                        const startHour = Math.round(lastEndHour * 4) / 4;
                        const endHour = Math.round(range.startHour * 4) / 4;
                        if (endHour > startHour) {
                            // Calculate click position (center of the actual gap)
                            const gapCenterPct = hourToPct((lastEndHour + range.startHour) / 2);
                            const clickX = dayRect.left + (gapCenterPct / 100) * dayRect.width;
                            gaps.push({ startHour, endHour, clickX, clickY: midY });
                        }
                    }
                    lastEndHour = Math.max(lastEndHour, range.endHour);
                });

                // Check for gap at end of day
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

        # Convert to TimeSlot objects
        slots = []
        excluded = self.config.excluded_rooms

        for room_data in available_data:
            room = room_data["room"]

            # Skip excluded rooms
            if room in excluded:
                continue

            # Skip rooms not in priority list (unless fallback enabled)
            if room not in self.config.priority_rooms and not self.config.fallback_enabled:
                continue

            for slot_data in room_data["slots"]:
                start_h = int(slot_data['startHour'])
                start_m = int((slot_data['startHour'] % 1) * 60)
                end_h = int(slot_data['endHour'])
                end_m = int((slot_data['endHour'] % 1) * 60)
                start_time = f"{start_h:02d}:{start_m:02d}"
                end_time = f"{end_h:02d}:{end_m:02d}"

                slot = TimeSlot(
                    room=room,
                    date=current_date,
                    start_time=start_time,
                    end_time=end_time,
                    available=True,
                    click_x=slot_data["clickX"],
                    click_y=slot_data["clickY"],
                )
                slots.append(slot)
                logger.debug("Found available slot: %s %s-%s", room, start_time, end_time)

        logger.info("Found %d available slots", len(slots))
        return slots

    def find_best_slot(self, slots: list[TimeSlot]) -> TimeSlot | None:
        """
        Find the best slot from a list based on chronological order, then room priority.

        Slots are sorted:
        1. Chronologically (earliest start time first)
        2. By room priority (preferred rooms first)

        Args:
            slots: List of available slots to choose from.

        Returns:
            The best TimeSlot, or None if no suitable slot found.
        """
        if not slots:
            return None

        # Filter out slots that can't be booked according to rules
        valid_slots = []
        for slot in slots:
            duration = self.rules.calculate_valid_duration(slot)
            if duration > 0:
                can_book, _ = self.rules.can_book_slot(slot, duration)
                if can_book:
                    valid_slots.append(slot)

        if not valid_slots:
            return None

        # Sort chronologically first, then by room priority
        def slot_priority(slot: TimeSlot) -> tuple:
            # Parse start time for chronological sorting
            start_h = int(slot.start_time.split(":")[0])
            start_m = int(slot.start_time.split(":")[1])
            start_minutes = start_h * 60 + start_m

            # Room priority (lower is better)
            try:
                room_priority = self.config.priority_rooms.index(slot.room)
            except ValueError:
                room_priority = 999

            # Sort by: time first, then room priority
            return (start_minutes, room_priority)

        sorted_slots = sorted(valid_slots, key=slot_priority)
        return sorted_slots[0] if sorted_slots else None

    def get_all_slots_sorted(self, slots: list[TimeSlot]) -> list[TimeSlot]:
        """
        Get all slots sorted chronologically then by room priority.

        Used for iterating through all slots when booking multiple.

        Args:
            slots: List of available slots.

        Returns:
            Sorted list of TimeSlot objects.
        """
        if not slots:
            return []

        def slot_priority(slot: TimeSlot) -> tuple:
            start_h = int(slot.start_time.split(":")[0])
            start_m = int(slot.start_time.split(":")[1])
            start_minutes = start_h * 60 + start_m

            try:
                room_priority = self.config.priority_rooms.index(slot.room)
            except ValueError:
                room_priority = 999

            return (start_minutes, room_priority)

        return sorted(slots, key=slot_priority)

    def book_slot(self, slot: TimeSlot, dry_run: bool = False) -> BookingResult:
        """
        Book a specific time slot by clicking on it.

        Args:
            slot: The TimeSlot to book.
            dry_run: If True, don't actually make the booking.

        Returns:
            BookingResult indicating success or failure.
        """
        date_str = slot.date.strftime("%Y-%m-%d") if slot.date else "unknown"

        # Calculate booking duration (capped at max)
        booking_duration = self.rules.calculate_valid_duration(slot)
        if booking_duration == 0:
            return BookingResult(
                success=False,
                room=slot.room,
                message=f"Slot duration invalid (min {self.config.min_duration_minutes} min)"
            )

        # Check rules before attempting
        can_book, reason = self.rules.can_book_slot(slot, booking_duration)
        if not can_book:
            logger.info("Skipping slot due to rules: %s", reason)
            return BookingResult(success=False, room=slot.room, message=reason)

        # Calculate actual end time for booking
        start_parts = slot.start_time.split(":")
        start_h = int(start_parts[0]) + int(start_parts[1]) / 60
        end_h = start_h + booking_duration / 60
        book_end_h = int(end_h)
        book_end_m = int((end_h % 1) * 60)
        book_end_time = f"{book_end_h:02d}:{book_end_m:02d}"

        if dry_run:
            logger.info("[DRY RUN] Would book: %s at %s-%s on %s",
                       slot.room, slot.start_time, book_end_time, date_str)
            return BookingResult(
                success=True,
                room=slot.room,
                date=date_str,
                start_time=slot.start_time,
                end_time=book_end_time,
                message="Dry run - no booking made",
            )

        try:
            logger.info("Attempting to book: %s at %s-%s on %s",
                       slot.room, slot.start_time, book_end_time, date_str)

            if slot.click_x is None or slot.click_y is None:
                logger.error("No click coordinates stored for this slot")
                return BookingResult(success=False, room=slot.room, message="No coordinates to click")

            # Step 1: Click on the empty grid space
            self.page.mouse.click(slot.click_x, slot.click_y)
            self.page.wait_for_timeout(2000)

            # Step 2: A popup appears - click "Student booking provisional"
            student_booking_btn = self.page.locator("mat-list-item:has-text('Student booking provisional')").first
            if student_booking_btn.count() > 0 and student_booking_btn.is_visible():
                logger.debug("Clicking 'Student booking provisional'")
                student_booking_btn.click()
                self.page.wait_for_timeout(3000)
            else:
                # Try alternative selector
                alt_booking = self.page.locator("text=Student booking provisional").first
                if alt_booking.count() > 0 and alt_booking.is_visible():
                    logger.debug("Clicking alternative student booking option")
                    alt_booking.click()
                    self.page.wait_for_timeout(3000)
                else:
                    logger.warning("No booking popup found after clicking slot")
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_timeout(1000)
                    return BookingResult(
                        success=False,
                        room=slot.room,
                        message="No booking popup appeared"
                    )

            # Step 3: Set the booking times using the time input fields
            try:
                # Set start time
                start_input = self.page.locator("#startDate").first
                if start_input.count() > 0 and start_input.is_visible():
                    start_input.click()
                    start_input.fill(slot.start_time)
                    self.page.wait_for_timeout(500)

                # Set end time
                end_input = self.page.locator("#endDate").first
                if end_input.count() > 0 and end_input.is_visible():
                    end_input.click()
                    end_input.fill(book_end_time)
                    self.page.wait_for_timeout(500)

                # Click near Save button to dismiss any dropdown
                save_btn = self.page.locator("button:has-text('Save')").first
                if save_btn.count() > 0:
                    save_box = save_btn.bounding_box()
                    if save_box:
                        self.page.mouse.click(save_box["x"] + 10, save_box["y"] - 20)
                        self.page.wait_for_timeout(500)

            except Exception as e:
                logger.warning("Error setting times: %s", e)

            # Step 4: Check for error messages
            # Check for "You are not allowed" error
            error_msg = self.page.locator("text=You are not allowed").first
            if error_msg.count() > 0 and error_msg.is_visible():
                error_text = error_msg.text_content() or "Room not bookable"
                logger.warning("Cannot book %s: %s", slot.room, error_text)

                # Extract conflict time range from error if present
                conflict_text = self.page.locator("text=/\\d{2}:\\d{2}\\s*-\\s*\\d{2}:\\d{2}.*Reservation/").first
                if conflict_text.count() > 0:
                    text = conflict_text.text_content() or ""
                    match = re.search(r'(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})', text)
                    if match:
                        c_start = int(match.group(1)) + int(match.group(2)) / 60
                        c_end = int(match.group(3)) + int(match.group(4)) / 60
                        self.rules.add_conflict_range(c_start, c_end)

                # Go back
                back_btn = self.page.locator("mat-icon:text('chevron_left')").first
                if back_btn.count() > 0 and back_btn.is_visible():
                    back_btn.click()
                else:
                    self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(2000)
                return BookingResult(
                    success=False,
                    room=slot.room,
                    message=f"Not allowed: {error_text[:50]}"
                )

            # Check for "conflicting events" or "resolve the conflicts"
            conflict_msg = self.page.locator("text=conflicting events, text=resolve the conflicts").first
            if conflict_msg.count() > 0 and conflict_msg.is_visible():
                logger.warning("Booking conflicts with existing reservation")
                back_btn = self.page.locator("mat-icon:text('chevron_left')").first
                if back_btn.count() > 0:
                    back_btn.click()
                self.page.wait_for_timeout(2000)
                return BookingResult(
                    success=False,
                    room=slot.room,
                    message="Conflicts with existing booking"
                )

            # Step 5: Click Save button
            save_btn = self.page.locator("button:has-text('Save')").first
            if save_btn.count() > 0 and save_btn.is_visible():
                # Check if Save button is enabled (has check icon, not remove/block)
                save_icon = save_btn.locator("mat-icon").first
                if save_icon.count() > 0:
                    icon_text = save_icon.text_content() or ""
                    if "remove" in icon_text or "block" in icon_text:
                        logger.warning("Save button is disabled")
                        back_btn = self.page.locator("mat-icon:text('chevron_left')").first
                        if back_btn.count() > 0:
                            back_btn.click()
                        self.page.wait_for_timeout(2000)
                        return BookingResult(
                            success=False,
                            room=slot.room,
                            message="Save button disabled - booking not allowed"
                        )

                logger.debug("Clicking Save button")
                save_btn.click()
                self.page.wait_for_timeout(3000)
            else:
                logger.warning("Save button not found")
                return BookingResult(
                    success=False,
                    room=slot.room,
                    message="Save button not found on booking page"
                )

            # Step 6: Verify booking was successful
            # Check if we're back on calendar (startDate input no longer visible)
            start_input = self.page.locator("#startDate").first
            if start_input.count() > 0 and start_input.is_visible():
                # Still on booking page - save may have failed
                post_error = self.page.locator("text=error, text=Error, text=failed, text=Failed, text=not allowed").first
                if post_error.count() > 0 and post_error.is_visible():
                    error_text = post_error.text_content() or "Unknown error"
                    back_btn = self.page.locator("mat-icon:text('chevron_left')").first
                    if back_btn.count() > 0:
                        back_btn.click()
                    self.page.wait_for_timeout(2000)
                    return BookingResult(
                        success=False,
                        room=slot.room,
                        message=f"Booking failed: {error_text[:50]}"
                    )

            # Success! Add to tracked bookings
            if slot.date:
                self.rules.add_existing_booking(ExistingBooking(
                    room=slot.room,
                    date=slot.date,
                    start_time=slot.start_time,
                    end_time=book_end_time
                ))

            logger.info("Successfully booked %s at %s-%s on %s",
                       slot.room, slot.start_time, book_end_time, date_str)
            return BookingResult(
                success=True,
                room=slot.room,
                date=date_str,
                start_time=slot.start_time,
                end_time=book_end_time,
                message="Booking confirmed",
            )

        except Exception as e:
            logger.error("Error booking slot: %s", e)
            return BookingResult(success=False, room=slot.room, message=str(e))

    def run_booking_cycle(self, dry_run: bool = False) -> list[BookingResult]:
        """
        Run a complete booking cycle for ALL days within the booking horizon.

        This checks each day from tomorrow up to max_horizon_days and attempts
        to book available slots, respecting per-room horizon limits.

        Args:
            dry_run: If True, don't actually make bookings.

        Returns:
            List of BookingResult for each booking attempt.
        """
        results = []
        bookings_made = 0
        max_bookings = self.config.max_bookings_per_run

        # Navigate to location first
        if not self.navigate_to_location():
            return [BookingResult(success=False, message="Failed to navigate to location")]

        # Check each day from tomorrow up to max horizon
        today = datetime.now().date()

        for days_ahead in range(1, self.config.max_horizon_days + 1):
            if bookings_made >= max_bookings:
                logger.info("Reached max bookings per run (%d)", max_bookings)
                break

            target_date = datetime.combine(today + timedelta(days=days_ahead), datetime.min.time())

            # Check if we should book on this day of week
            if self.config.preferred_days:
                if target_date.weekday() not in self.config.preferred_days:
                    logger.debug("Skipping %s - not a preferred booking day",
                               target_date.strftime("%A"))
                    continue

            logger.info("Checking availability for %s (%d days ahead)",
                       target_date.strftime("%Y-%m-%d"), days_ahead)

            # Navigate to this date
            if not self.navigate_to_date(target_date):
                results.append(BookingResult(
                    success=False,
                    date=target_date.strftime("%Y-%m-%d"),
                    message="Failed to navigate to date"
                ))
                continue

            # Get available slots
            all_slots = self.get_available_slots()

            # Filter by per-room horizon
            eligible_slots = []
            for slot in all_slots:
                room_horizon = self.config.get_room_horizon(slot.room)
                if days_ahead <= room_horizon:
                    eligible_slots.append(slot)
                else:
                    logger.debug("Room %s: %d days ahead exceeds horizon of %d",
                               slot.room, days_ahead, room_horizon)

            if not eligible_slots:
                logger.info("No available slots for %s", target_date.strftime("%Y-%m-%d"))
                continue

            # Find and book the best slot
            best_slot = self.find_best_slot(eligible_slots)
            if best_slot:
                result = self.book_slot(best_slot, dry_run=dry_run)
                results.append(result)

                if result.success:
                    bookings_made += 1
                    logger.info("Booked: %s at %s on %s",
                              result.room, result.start_time, result.date)

        if not results:
            results.append(BookingResult(success=False, message="No slots available in booking window"))

        return results

    # Backward compatibility method
    def run_booking_cycle_single(self, dry_run: bool = False) -> BookingResult:
        """
        Run booking cycle and return single result (for backward compatibility).
        """
        results = self.run_booking_cycle(dry_run=dry_run)
        for r in results:
            if r.success:
                return r
        return results[0] if results else BookingResult(success=False, message="No results")
