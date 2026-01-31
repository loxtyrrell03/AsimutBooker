"""Test booking on February 2nd (closer date, should work with most rooms)."""

from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright

state_file = Path("data/browser_state/state.json")

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

    # Navigate to Feb 2 (2 days from Jan 31)
    print("Navigating to Feb 2...")
    for _ in range(2):
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

    # Track time ranges where we have conflicts (will be populated as we encounter them)
    my_conflict_ranges = []

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

    # Build list of all available slots - will sort by time then room priority
    all_slots = []
    for room_data in available_data:
        room_name = room_data["room"]
        if room_name not in PRIORITY_ROOMS:
            continue

        room_priority = PRIORITY_ROOMS.index(room_name)

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
                "start_hour": slot['startHour'],  # For sorting
                "room_priority": room_priority,   # For sorting
                "start_time": f"{start_h:02d}:{start_m:02d}",
                "end_time": f"{end_h:02d}:{end_m:02d}",
                "book_start": f"{start_h:02d}:{start_m:02d}",
                "book_end": f"{book_end_h:02d}:{book_end_m:02d}",
                "click_x": slot["clickX"],
                "click_y": slot["clickY"]
            })

    # Sort by start time first, then by room priority
    all_slots.sort(key=lambda s: (s['start_hour'], s['room_priority']))

    if not all_slots:
        print("No available slots found in priority rooms!")
        page.wait_for_timeout(10000)
        context.close()
        browser.close()
        exit()

    print(f"\nFound {len(all_slots)} available slots across priority rooms")
    for slot in all_slots[:15]:
        print(f"  {slot['room']}: {slot['start_time']}-{slot['end_time']} (will book {slot['book_start']}-{slot['book_end']})")

    print("\nAttempting booking in 3 seconds...")
    page.wait_for_timeout(3000)

    def go_back():
        """Helper to go back to calendar."""
        back_btn = page.locator("mat-icon:text('chevron_left')").first
        if back_btn.count() > 0:
            try:
                back_btn.click(force=True)
            except:
                page.keyboard.press("Escape")
        page.wait_for_timeout(2000)

    # Try each slot until one succeeds
    booked = False
    for slot_idx, slot in enumerate(all_slots):
        if slot_idx >= 20:  # Limit attempts
            break

        # Check if this slot overlaps with any known conflict ranges
        slot_start = slot['start_hour']
        slot_end = slot_start + 2  # Max 2 hour booking

        overlaps_conflict = False
        for conflict_start, conflict_end in my_conflict_ranges:
            if slot_start < conflict_end and slot_end > conflict_start:
                overlaps_conflict = True
                break

        if overlaps_conflict:
            print(f"\nSkipping {slot['room']} at {slot['book_start']} - overlaps with your existing reservation")
            continue

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
                print("  No popup found - skipping")
                page.keyboard.press("Escape")
                page.wait_for_timeout(1000)
                continue

        # Wait for the booking page to load
        print("  Waiting for booking page to load...")
        page.wait_for_timeout(3000)

        # Step 3: Set the start and end times
        print("Step 3: Setting booking times...")

        # Use specific IDs for time inputs
        start_input = page.locator("#startDate")
        end_input = page.locator("#endDate")

        if start_input.count() > 0 and end_input.count() > 0:
            current_start = start_input.input_value()
            current_end = end_input.input_value()
            print(f"  Current times: {current_start} - {current_end}")
            print(f"  Target times: {slot['book_start']} - {slot['book_end']}")

            # Set start time
            if current_start != slot['book_start']:
                print(f"  Setting start time to {slot['book_start']}...")
                start_input.click()
                page.wait_for_timeout(300)
                start_input.fill("")  # Clear first
                start_input.fill(slot['book_start'])
                page.wait_for_timeout(300)

            # Set end time
            if current_end != slot['book_end']:
                print(f"  Setting end time to {slot['book_end']}...")
                end_input.click()
                page.wait_for_timeout(300)
                end_input.fill("")  # Clear first
                end_input.fill(slot['book_end'])
                page.wait_for_timeout(300)

            # Click on Save button area to close the time dropdown
            # The Save button is at the bottom of the form
            save_btn = page.locator("button:has-text('Save')").first
            if save_btn.count() > 0:
                # Get the button's bounding box and click near it (but not on it)
                box = save_btn.bounding_box()
                if box:
                    # Click just above the save button to close dropdown without triggering save
                    page.mouse.click(box['x'] + box['width'] / 2, box['y'] - 20)
                else:
                    page.mouse.click(450, 500)  # Fallback position
            else:
                page.mouse.click(450, 500)  # Fallback position
            page.wait_for_timeout(500)

            # Verify times were set
            new_start = start_input.input_value()
            new_end = end_input.input_value()
            print(f"  After setting: {new_start} - {new_end}")
        else:
            print("  Could not find time inputs by ID, trying alternative...")

        page.wait_for_timeout(1000)

        # Step 4: Check for error messages
        print("Step 4: Checking for error messages...")

        error_msg = page.locator("text=You are not allowed").first
        if error_msg.count() > 0 and error_msg.is_visible():
            error_text = error_msg.text_content()
            print(f"  SKIP: {error_text[:80]}...")
            go_back()
            continue

        # Check for clash/conflict warnings
        clash_msg = page.locator("text=booking clashes").first
        conflict_msg = page.locator("text=conflicting events").first
        resolve_msg = page.locator("text=resolve the conflicts").first

        if (clash_msg.count() > 0 and clash_msg.is_visible()) or \
           (conflict_msg.count() > 0 and conflict_msg.is_visible()) or \
           (resolve_msg.count() > 0 and resolve_msg.is_visible()):
            # Extract the conflict time range if possible
            # Look for text like "07:30 - 09:00 Reservation"
            conflict_text = page.locator("text=/\\d{2}:\\d{2}\\s*-\\s*\\d{2}:\\d{2}.*Reservation/").first
            if conflict_text.count() > 0:
                try:
                    text = conflict_text.text_content()
                    import re
                    match = re.search(r'(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})', text)
                    if match:
                        c_start = int(match.group(1)) + int(match.group(2)) / 60
                        c_end = int(match.group(3)) + int(match.group(4)) / 60
                        my_conflict_ranges.append((c_start, c_end))
                        print(f"  SKIP: You have a reservation from {match.group(1)}:{match.group(2)} - {match.group(3)}:{match.group(4)}")
                except:
                    print("  SKIP: Booking conflicts with existing reservation")
            else:
                print("  SKIP: Booking conflicts with existing reservation")
            go_back()
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
            # Check if save button is enabled (shows checkmark, not minus/remove icon)
            save_icon = save_btn.locator("mat-icon").first
            if save_icon.count() > 0:
                icon_text = save_icon.text_content() or ""
                # "check" or "done" = enabled, "remove" or "block" = disabled
                if icon_text in ["remove", "block", "close"]:
                    print(f"  Save button shows '{icon_text}' icon - cannot save")
                    go_back()
                    continue
                print(f"  Save button icon: '{icon_text}'")

            print("  Clicking Save...")
            save_btn.click()
            page.wait_for_timeout(3000)

            # Check if save was successful
            # After successful save, we should be back on calendar (location-day elements visible)
            # or there might be new error messages
            calendar_visible = page.locator(".location-day").first
            new_conflict = page.locator("text=conflicting events").first
            new_not_allowed = page.locator("text=not allowed").first

            if new_conflict.count() > 0 and new_conflict.is_visible():
                print("  Save failed - conflict detected after save")
                go_back()
                continue

            if new_not_allowed.count() > 0 and new_not_allowed.is_visible():
                print("  Save failed - not allowed")
                go_back()
                continue

            # Check if we're back on calendar OR if booking was successful
            # The booking form should be gone after successful save
            booking_form = page.locator("#startDate")  # Time input only exists on booking form
            if booking_form.count() == 0 or not booking_form.is_visible():
                booked = True

                print("\n" + "="*50)
                print("BOOKING COMPLETE!")
                print("="*50)
                print(f"Room: {slot['room']}")
                print(f"Time: {slot['book_start']} - {slot['book_end']}")
                print(f"Date: February 2, 2026")
                print("="*50)

                page.screenshot(path="debug_screenshots/booking_complete.png")
                print("\nScreenshot saved to debug_screenshots/booking_complete.png")
                break
            else:
                print("  Still on booking form - save may have failed")
                go_back()
                continue
        else:
            print("  Save button not found - skipping")
            go_back()
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
