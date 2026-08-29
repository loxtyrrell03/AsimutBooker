"""DANGEROUS manual tool that can edit a real existing reservation.

This will:
1. Go to your agenda
2. Find the first reservation
3. Open edit mode
4. Change the end time by +15 minutes
5. Save

Usage:
    python tools/manual_live/edit_reservation_live.py --dry-run
    python tools/manual_live/edit_reservation_live.py  # DANGEROUS: edits

Read README.md before use. ``--headless`` changes only browser visibility; it
does not prevent an edit.
"""

import argparse
import re
from pathlib import Path
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
state_file = REPO_ROOT / "data" / "browser_state" / "state.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Find reservation but don't edit")
    parser.add_argument("--date", type=str, help="Target date (e.g., '2026-02-03' or 'Monday')")
    parser.add_argument("--time", type=str, help="Target start time (e.g., '09:00')")
    parser.add_argument("--room", type=str, help="Target room (e.g., 'B0.13')")
    args = parser.parse_args()

    # Parse target criteria if provided
    target_date = args.date
    target_time = args.time
    target_room = args.room

    if target_date or target_time or target_room:
        print(f"Looking for specific booking:")
        if target_date:
            print(f"  Date: {target_date}")
        if target_time:
            print(f"  Time: {target_time}")
        if target_room:
            print(f"  Room: {target_room}")

    print("="*60)
    print("TEST: Edit Any Reservation")
    print("="*60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(
            storage_state=str(state_file),
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()

        # 1. Navigate to agenda
        print("\n1. Navigating to agenda...")
        page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
        page.wait_for_timeout(2000)

        # 2. Find any reservation
        print("\n2. Looking for reservations...")

        reservation_card = None
        reservation_info = None

        # Only scroll if searching for any reservation (not a specific one)
        # When targeting a specific booking, we search without scrolling first
        should_scroll = not (target_room and target_time)

        # Get current date/time for filtering
        now = datetime.now()
        current_time_mins = now.hour * 60 + now.minute
        print(f"   Current time: {now.strftime('%Y-%m-%d %H:%M')}")

        # Helper function to check if an event is cancelled
        def is_cancelled(panel):
            """Check if event panel indicates a cancelled event."""
            try:
                panel_text = panel.inner_text(timeout=500)

                # Check for "Cancelled" text in the panel
                if 'Cancelled' in panel_text or 'CANCELLED' in panel_text:
                    return True

                # Check using JavaScript - look at the event-datetime element for strikethrough
                is_cancelled_js = page.evaluate("""(element) => {
                    // Check the event-datetime element specifically (where time is shown)
                    const datetimeEl = element.querySelector('[data-cy="event-datetime"], .event-datetime');
                    if (datetimeEl) {
                        const style = window.getComputedStyle(datetimeEl);
                        if (style.textDecoration.includes('line-through') ||
                            style.textDecorationLine.includes('line-through')) {
                            return true;
                        }
                    }

                    // Also check all child elements for strikethrough
                    const allElements = element.querySelectorAll('*');
                    for (const el of allElements) {
                        const style = window.getComputedStyle(el);
                        if (style.textDecoration.includes('line-through') ||
                            style.textDecorationLine.includes('line-through')) {
                            return true;
                        }
                    }

                    // Check for cancelled class names anywhere in the panel
                    const className = element.className || '';
                    const innerHTML = element.innerHTML || '';
                    if (className.includes('cancelled') || className.includes('canceled') ||
                        innerHTML.includes('cancelled') || innerHTML.includes('canceled')) {
                        return true;
                    }

                    return false;
                }""", panel.element_handle())
                return is_cancelled_js
            except:
                return False

        # If searching for a specific booking, use JavaScript to find it efficiently
        if target_room and target_time:
            print(f"   Searching for specific booking: {target_room} at {target_time}...")

            # Search function to find matching panel
            def find_matching_panel():
                return page.evaluate("""(args) => {
                const { room, time } = args;
                const panels = document.querySelectorAll('.as-event-panel');

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
            }""", {"room": target_room, "time": target_time})

            # First try without scrolling
            matching_index = find_matching_panel()

            # If not found, scroll down and try again (booking may be further in future)
            if matching_index < 0:
                print(f"   Not found in visible area, scrolling to find it...")
                for scroll_attempt in range(10):
                    page.evaluate("window.scrollBy(0, 2000)")
                    page.wait_for_timeout(300)
                    matching_index = find_matching_panel()
                    if matching_index >= 0:
                        break

            if matching_index >= 0:
                all_panels = page.locator('.as-event-panel').all()
                reservation_card = all_panels[matching_index]
                panel_text = reservation_card.inner_text(timeout=500)
                reservation_info = panel_text.replace('\n', ' ')[:100]
                print(f"   FOUND MATCH (panel #{matching_index}): {reservation_info}...")
            else:
                print(f"   No matching reservation found for {target_room} at {target_time}")

        else:
            # Original logic for finding any future reservation - need to scroll first
            print("   Scrolling to bottom of agenda to find future events...")
            for _ in range(20):
                page.evaluate("window.scrollBy(0, 1000)")
                page.wait_for_timeout(200)
            page.wait_for_timeout(1000)

            event_cards = page.locator('.as-event-panel').all()
            print(f"   Found {len(event_cards)} event panels")

            sample_count = 0
            for card in reversed(event_cards):
                try:
                    card_text = card.inner_text(timeout=500)

                    has_reservation_title = 'Reservation' in card_text
                    is_practice_room = 'in B0.' in card_text or 'in B1.' in card_text or 'in A3.39' in card_text
                    is_not_class = 'BMus' not in card_text and 'MMus' not in card_text and 'Harmony' not in card_text

                    # Extract time
                    time_match = re.search(r'(\d{2}):(\d{2})\s*-\s*(\d{2}):(\d{2})', card_text)
                    event_time = None
                    if time_match:
                        event_time = (int(time_match.group(1)), int(time_match.group(2)))

                    # Since we're iterating from bottom (future) we assume these are future events
                    is_future = event_time is not None

                    if has_reservation_title and is_practice_room and is_not_class and is_future:
                        # Check if cancelled
                        if is_cancelled(card):
                            print(f"   [skip] Found reservation but it's CANCELLED")
                            continue
                        reservation_card = card
                        reservation_info = card_text.replace('\n', ' ')[:100]
                        print(f"   FOUND: {reservation_info}...")
                        break
                    elif sample_count < 3 and 'Reservation' in card_text:
                        print(f"   [sample] {card_text.replace(chr(10), ' ')[:80]}...")
                        sample_count += 1
                except:
                    continue

        if not reservation_card:
            print("   No reservations found!")
            context.close()
            browser.close()
            return

        # 3. Scroll the reservation into view and click its more_vert button directly
        print("\n3. Scrolling to reservation...")
        reservation_card.scroll_into_view_if_needed()
        page.wait_for_timeout(500)

        # 4. Click the more_vert button ON THIS SPECIFIC CARD
        print("\n4. Looking for options button on this card...")

        # Find the more_vert button within this specific card
        more_btn = reservation_card.locator("button[data-cy='button_more']").first
        if more_btn.count() == 0:
            more_btn = reservation_card.locator(".as-event-options-button").first
        if more_btn.count() == 0:
            more_btn = reservation_card.locator("button:has(mat-icon:has-text('more_vert'))").first
        if more_btn.count() == 0:
            more_btn = reservation_card.locator("mat-icon:has-text('more_vert')").first

        if more_btn.count() > 0 and more_btn.is_visible():
            print("   Found options button on card, clicking...")
            more_btn.click()
            page.wait_for_timeout(500)
        else:
            print("   Could not find options button!")
            print("   Taking screenshot...")
            page.screenshot(path="debug_no_options_btn.png")
            if not args.headless:
                print("   Keeping browser open for inspection...")
                page.wait_for_timeout(30000)
            context.close()
            browser.close()
            return

        # 5. Click "Edit event"
        print("\n5. Looking for 'Edit event' option...")

        edit_option = page.locator("mat-list-item").filter(has_text="Edit event").first
        if edit_option.count() == 0:
            edit_option = page.locator("mat-list-item:has(mat-icon:has-text('edit'))").first

        if edit_option.count() > 0 and edit_option.is_visible():
            print("   Found 'Edit event', clicking...")
            edit_option.click()
            page.wait_for_timeout(1500)
        else:
            print("   Could not find 'Edit event' option!")
            print("   Available options:")
            options = page.locator("mat-list-item").all()
            for opt in options:
                try:
                    print(f"     - {opt.inner_text(timeout=500)}")
                except:
                    pass
            page.screenshot(path="debug_no_edit_option.png")
            if not args.headless:
                page.wait_for_timeout(30000)
            context.close()
            browser.close()
            return

        # 6. Find the end time input (use same selector as book_week.py)
        print("\n6. Looking for end time input...")

        end_input = page.locator("#endDate").first
        if end_input.count() == 0:
            end_input = page.locator("input[data-cy='time-range-end-time']").first

        if end_input.count() > 0 and end_input.is_visible():
            current_value = end_input.input_value()
            print(f"   Found end time input, current value: {current_value}")

            if args.dry_run:
                print("\n   [DRY RUN] Would change end time here.")
                print("   Pressing Escape to cancel...")
                page.keyboard.press("Escape")
            else:
                # Parse current time and add 15 minutes
                try:
                    parts = current_value.split(':')
                    hour = int(parts[0])
                    minute = int(parts[1]) if len(parts) > 1 else 0

                    # Add 15 minutes
                    minute += 15
                    if minute >= 60:
                        minute -= 60
                        hour += 1

                    new_time = f"{hour:02d}:{minute:02d}"
                    print(f"   Changing end time to: {new_time}")

                    # Click, fill the new time
                    end_input.click()
                    end_input.fill(new_time)
                    page.wait_for_timeout(300)

                    # Click somewhere random on the right side to dismiss any dropdown
                    # Get viewport size and click on the right side
                    viewport = page.viewport_size
                    if viewport:
                        # Click on the right third of the page, middle height
                        page.mouse.click(viewport['width'] - 100, viewport['height'] // 2)
                    page.wait_for_timeout(500)

                    # 7. Click Save
                    print("\n7. Looking for Save button...")
                    save_btn = page.locator("button:has-text('Save')").first

                    if save_btn.count() > 0 and save_btn.is_visible():
                        print("   Clicking Save...")
                        save_btn.click()
                        page.wait_for_timeout(2000)
                        print("\n   SUCCESS! Reservation extended.")

                        # 8. Navigate back to agenda
                        print("\n8. Navigating back to agenda...")
                        page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
                        print("   Back on agenda page.")
                    else:
                        print("   Could not find Save button!")
                        page.screenshot(path="debug_no_save_btn.png")
                except Exception as e:
                    print(f"   Error: {e}")
        else:
            print("   Could not find end time input!")
            print("   Taking screenshot...")
            page.screenshot(path="debug_no_end_input.png")

            # Show what inputs are available
            print("   Available inputs:")
            inputs = page.locator("input").all()
            for inp in inputs:
                try:
                    inp_id = inp.get_attribute("id") or "no-id"
                    inp_class = inp.get_attribute("class") or "no-class"
                    inp_val = inp.input_value() or "empty"
                    print(f"     - id={inp_id}, class={inp_class[:30]}, value={inp_val}")
                except:
                    pass

        if not args.headless:
            print("\n   Keeping browser open for 10 seconds...")
            page.wait_for_timeout(10000)

        context.close()
        browser.close()

    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60)


if __name__ == "__main__":
    main()
