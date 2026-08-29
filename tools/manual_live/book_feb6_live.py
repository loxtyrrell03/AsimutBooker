"""DANGEROUS live-site booking script retained for manual investigation only.

Running this file opens the real RWCMD Asimut site and may click Save to create
a real reservation. It has no dry-run mode. See README.md before use.
"""

from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
state_file = REPO_ROOT / "data" / "browser_state" / "state.json"

# Maximum booking duration in hours
MAX_BOOKING_HOURS = 2

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        storage_state=str(state_file),
        viewport={"width": 1920, "height": 1080}
    )
    page = context.new_page()

    # Navigate to practice rooms
    print("Navigating to practice rooms...")
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

    # Navigate to Feb 6 (6 days from Jan 31)
    print("Navigating to Feb 6...")
    for _ in range(6):
        arrow = page.locator("mat-icon:text('chevron_right')").first
        arrow.click()
        page.wait_for_timeout(1000)

    page.wait_for_timeout(2000)

    # Priority rooms in order
    PRIORITY_ROOMS = [
        "B0.29", "B1.09", "B0.11", "B1.16", "B0.15", "B0.13", "B0.27",
        "B1.14", "B1.17", "B1.20", "B1.18", "B1.21", "B1.19",
        "B1.06", "B1.07", "B1.08", "B1.10", "B1.11"
    ]

    # Find available slots using the same detection logic
    print("\nFinding available slots...")
    available_data = page.evaluate("""() => {
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

    # Build list of all available slots in priority order
    all_slots = []
    for room_name in PRIORITY_ROOMS:
        for room_data in available_data:
            if room_data["room"] == room_name:
                for slot in room_data["slots"]:
                    start_h = int(slot['startHour'])
                    start_m = int((slot['startHour'] % 1) * 60)
                    end_h = int(slot['endHour'])
                    end_m = int((slot['endHour'] % 1) * 60)

                    # Calculate booking end time (max 2 hours or end of slot)
                    slot_duration = slot['endHour'] - slot['startHour']
                    booking_duration = min(slot_duration, MAX_BOOKING_HOURS)
                    booking_end_hour = slot['startHour'] + booking_duration
                    book_end_h = int(booking_end_hour)
                    book_end_m = int((booking_end_hour % 1) * 60)

                    all_slots.append({
                        "room": room_name,
                        "start_time": f"{start_h:02d}:{start_m:02d}",
                        "end_time": f"{end_h:02d}:{end_m:02d}",
                        "book_start": f"{start_h:02d}:{start_m:02d}",
                        "book_end": f"{book_end_h:02d}:{book_end_m:02d}",
                        "click_x": slot["clickX"],
                        "click_y": slot["clickY"]
                    })

    if not all_slots:
        print("No available slots found in priority rooms!")
        page.wait_for_timeout(10000)
        context.close()
        browser.close()
        exit()

    print(f"\nFound {len(all_slots)} available slots across priority rooms")
    for slot in all_slots[:10]:
        print(f"  {slot['room']}: {slot['start_time']}-{slot['end_time']} (will book {slot['book_start']}-{slot['book_end']})")

    print("\nAttempting booking in 3 seconds...")
    page.wait_for_timeout(3000)

    # Try each slot until one succeeds
    booked = False
    for slot_idx, slot in enumerate(all_slots):
        print(f"\n{'='*50}")
        print(f"Trying slot {slot_idx + 1}/{len(all_slots)}: {slot['room']} at {slot['book_start']}-{slot['book_end']}")
        print(f"{'='*50}")

        # Step 1: Click on the empty slot
        print("\nStep 1: Clicking on empty slot...")
        page.mouse.click(slot["click_x"], slot["click_y"])
        page.wait_for_timeout(2000)

        # Step 2: Click "Student booking provisional"
        print("Step 2: Looking for 'Student booking provisional'...")
        student_booking_btn = page.locator("mat-list-item:has-text('Student booking provisional')").first
        if student_booking_btn.count() > 0 and student_booking_btn.is_visible():
            print("  Found - clicking...")
            student_booking_btn.click()
        else:
            student_booking_btn = page.locator("text=Student booking provisional").first
            if student_booking_btn.count() > 0 and student_booking_btn.is_visible():
                print("  Found by text - clicking...")
                student_booking_btn.click()
            else:
                print("  No popup found - clicking elsewhere to dismiss and retry")
                page.keyboard.press("Escape")
                page.wait_for_timeout(1000)
                continue

        # Wait for the booking page to load
        print("  Waiting for booking page to load...")
        page.wait_for_timeout(3000)

        # Step 3: Set the start and end times
        print("Step 3: Setting booking times...")

        # Find and set start time - look for input fields with time values
        # The time inputs should be in a container near the date
        time_inputs = page.locator("input[type='time'], input[placeholder*='time'], .time-input input").all()
        print(f"  Found {len(time_inputs)} time inputs")

        # Try to find time display elements and click to edit
        # Based on screenshot, times appear as "08:15 - 09:15" in clickable fields
        start_time_field = page.locator("input").filter(has_text=slot['book_start'][:2]).first

        # Alternative: look for the time container and input the times
        # The booking form likely has input fields for start/end time
        try:
            # Try clicking on the start time area and typing
            time_container = page.locator(".time-picker, [class*='time'], input[type='text']").first
            if time_container.count() > 0:
                print(f"  Found time container")

            # Look for specific time input fields
            # Start time input
            start_input = page.locator("input").nth(0)  # First input might be start time
            end_input = page.locator("input").nth(1)    # Second might be end time

            # Check what inputs exist
            all_inputs = page.locator("input:visible").all()
            print(f"  Found {len(all_inputs)} visible inputs")
            for i, inp in enumerate(all_inputs[:5]):
                try:
                    val = inp.input_value()
                    placeholder = inp.get_attribute("placeholder") or ""
                    print(f"    Input {i}: value='{val}' placeholder='{placeholder}'")
                except:
                    pass

        except Exception as e:
            print(f"  Error finding time inputs: {e}")

        # For now, let's check if the default times work or need adjustment
        # The system might auto-populate times based on where we clicked

        page.wait_for_timeout(1000)

        # Step 4: Check for error messages
        print("Step 4: Checking for error messages...")
        error_msg = page.locator("text=You are not allowed").first
        if error_msg.count() > 0 and error_msg.is_visible():
            error_text = error_msg.text_content()
            print(f"  SKIP: {error_text[:80]}...")
            # Go back using chevron_left
            back_btn = page.locator("mat-icon:text('chevron_left')").first
            if back_btn.count() > 0 and back_btn.is_visible():
                back_btn.click()
            else:
                page.keyboard.press("Escape")
            page.wait_for_timeout(2000)
            continue

        # Check for clash warning
        clash_msg = page.locator("text=booking clashes").first
        if clash_msg.count() > 0 and clash_msg.is_visible():
            print("  SKIP: Booking clashes with existing reservation")
            back_btn = page.locator("mat-icon:text('chevron_left')").first
            if back_btn.count() > 0:
                back_btn.click()
            page.wait_for_timeout(2000)
            continue

        # Check for "does not have a location" error
        no_location = page.locator("text=does not currently have a location").first
        if no_location.count() > 0 and no_location.is_visible():
            print("  SKIP: Event does not have a location")
            back_btn = page.locator("mat-icon:text('chevron_left')").first
            if back_btn.count() > 0:
                back_btn.click()
            page.wait_for_timeout(2000)
            continue

        print("  No blocking errors found")

        # Take screenshot to see the state
        page.screenshot(path="debug_screenshots/booking_page.png")
        print("  Screenshot saved to debug_screenshots/booking_page.png")

        # Step 5: Click Save button
        print("Step 5: Looking for Save button...")
        page.wait_for_timeout(1000)

        save_btn = page.locator("button:has-text('Save')").first
        if save_btn.count() > 0 and save_btn.is_visible():
            # Check if save button is enabled (not greyed out)
            is_disabled = save_btn.get_attribute("disabled")
            if is_disabled:
                print("  Save button is disabled - checking why...")
                page.screenshot(path="debug_screenshots/save_disabled.png")
                back_btn = page.locator("mat-icon:text('chevron_left')").first
                if back_btn.count() > 0:
                    back_btn.click()
                page.wait_for_timeout(2000)
                continue

            print("  Found - clicking Save...")
            save_btn.click()
            page.wait_for_timeout(3000)

            # Check if we're still on booking page (save failed) or back to calendar (success)
            still_on_booking = page.locator("text=Reservation").first
            if still_on_booking.count() > 0 and still_on_booking.is_visible():
                # Check for new errors
                new_error = page.locator("text=not allowed, text=clashes, text=error").first
                if new_error.count() > 0 and new_error.is_visible():
                    print(f"  Save failed: {new_error.text_content()[:50]}")
                    back_btn = page.locator("mat-icon:text('chevron_left')").first
                    if back_btn.count() > 0:
                        back_btn.click()
                    page.wait_for_timeout(2000)
                    continue

            booked = True

            print("\n" + "="*50)
            print("BOOKING COMPLETE!")
            print("="*50)
            print(f"Room: {slot['room']}")
            print(f"Time: {slot['book_start']} - {slot['book_end']}")
            print(f"Date: February 6, 2026")
            print("="*50)

            page.screenshot(path="debug_screenshots/booking_complete.png")
            print("\nScreenshot saved to debug_screenshots/booking_complete.png")
            break
        else:
            print("  Save button not found - skipping")
            back_btn = page.locator("mat-icon:text('chevron_left')").first
            if back_btn.count() > 0:
                back_btn.click()
            page.wait_for_timeout(2000)
            continue

    if not booked:
        print("\n" + "="*50)
        print("FAILED: Could not book any slot")
        print("="*50)
        page.screenshot(path="debug_screenshots/booking_failed.png")

    print("\nKeeping browser open for 15 seconds to verify...")
    page.wait_for_timeout(15000)

    context.close()
    browser.close()
