"""Core booking logic for Asimut practice rooms."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from playwright.sync_api import Page, Locator, TimeoutError as PlaywrightTimeout

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger(__name__)


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
        Find the best slot from a list based on room priority and preferred times.

        Args:
            slots: List of available slots to choose from.

        Returns:
            The best TimeSlot, or None if no suitable slot found.
        """
        if not slots:
            return None

        # Sort by room priority and preferred times
        def slot_priority(slot: TimeSlot) -> tuple:
            # Lower is better
            try:
                room_priority = self.config.priority_rooms.index(slot.room)
            except ValueError:
                room_priority = 999

            # Check if time matches preferred times
            time_priority = 999
            try:
                slot_hour = int(slot.start_time.split(":")[0])
                for i, pref in enumerate(self.config.preferred_times):
                    pref_start = int(pref["start"].split(":")[0])
                    pref_end = int(pref["end"].split(":")[0])
                    if pref_start <= slot_hour < pref_end:
                        time_priority = i
                        break
            except (ValueError, KeyError):
                pass

            return (room_priority, time_priority, slot.start_time)

        sorted_slots = sorted(slots, key=slot_priority)
        return sorted_slots[0] if sorted_slots else None

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

        if dry_run:
            logger.info("[DRY RUN] Would book: %s at %s on %s",
                       slot.room, slot.start_time, date_str)
            return BookingResult(
                success=True,
                room=slot.room,
                date=date_str,
                start_time=slot.start_time,
                end_time=slot.end_time,
                message="Dry run - no booking made",
            )

        try:
            logger.info("Attempting to book: %s at %s on %s", slot.room, slot.start_time, date_str)

            if slot.click_x is None or slot.click_y is None:
                logger.error("No click coordinates stored for this slot")
                return BookingResult(success=False, room=slot.room, message="No coordinates to click")

            # Step 1: Click on the empty grid space
            self.page.mouse.click(slot.click_x, slot.click_y)
            self.page.wait_for_timeout(1500)

            # Step 2: A popup appears - click "Student booking provisional"
            student_booking_btn = self.page.locator("text=Student booking provisional").first
            if student_booking_btn.count() > 0 and student_booking_btn.is_visible():
                logger.debug("Clicking 'Student booking provisional'")
                student_booking_btn.click()
                self.page.wait_for_timeout(2000)
            else:
                # Try alternative - might be "Student Chamber Music Provisional" etc
                alt_booking = self.page.locator("text=Student").filter(has_text="provisional").first
                if alt_booking.count() > 0 and alt_booking.is_visible():
                    logger.debug("Clicking alternative student booking option")
                    alt_booking.click()
                    self.page.wait_for_timeout(2000)
                else:
                    logger.warning("No booking popup found after clicking slot")
                    return BookingResult(
                        success=False,
                        room=slot.room,
                        message="No booking popup appeared"
                    )

            # Step 3: Now on booking page - check for error message
            # Error appears in yellow/cream colored box with "You are not allowed..."
            error_msg = self.page.locator("text=You are not allowed").first
            if error_msg.count() > 0 and error_msg.is_visible():
                error_text = error_msg.text_content() or "Room not bookable"
                logger.warning("Cannot book %s: %s", slot.room, error_text)
                # Close the dialog by clicking back arrow or X
                back_btn = self.page.locator("[class*='back'], mat-icon:text('arrow_back'), button:has(mat-icon:text('close'))").first
                if back_btn.count() > 0:
                    back_btn.click()
                    self.page.wait_for_timeout(1000)
                return BookingResult(
                    success=False,
                    room=slot.room,
                    message=f"Not allowed to book: {error_text[:50]}"
                )

            # Step 4: Verify the booking details look correct (optional logging)
            # The page shows the room name, date, and time
            logger.debug("Booking page loaded, looking for Save button")

            # Step 5: Click Save button to confirm booking
            save_btn = self.page.locator("button:has-text('Save')").first
            if save_btn.count() > 0 and save_btn.is_visible():
                logger.debug("Clicking Save button")
                save_btn.click()
                self.page.wait_for_timeout(2000)
            else:
                logger.warning("Save button not found")
                return BookingResult(
                    success=False,
                    room=slot.room,
                    message="Save button not found on booking page"
                )

            # Step 6: Verify booking was successful
            # After save, we should return to the calendar view
            # Check for any error messages that might appear
            post_error = self.page.locator("text=error, text=Error, text=failed, text=Failed").first
            if post_error.count() > 0 and post_error.is_visible():
                return BookingResult(
                    success=False,
                    room=slot.room,
                    message="Booking failed after save"
                )

            logger.info("Successfully booked %s at %s on %s", slot.room, slot.start_time, date_str)
            return BookingResult(
                success=True,
                room=slot.room,
                date=date_str,
                start_time=slot.start_time,
                end_time=slot.end_time,
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
