"""
Test script for verifying the find_matching_panel date matching fix.

This script connects to the REAL Asimut website using saved browser state
and tests the date matching logic against actual reservations.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# The improved JavaScript that includes date matching
FIND_MATCHING_PANEL_JS = """(args) => {
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
            console.log('Date mismatch for panel ' + i + ': ' + panelDate.year + '-' + (panelDate.month+1) + '-' + panelDate.day + ' vs ' + targetDate);
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
        console.log('Found matching panel ' + i + ' for ' + room + ' on ' + targetDate + ' at ' + time);
        return i;
    }
    return -1;
}"""

# JavaScript to extract all reservations from the agenda
EXTRACT_RESERVATIONS_JS = """() => {
    const reservations = [];
    const monthNames = ['January', 'February', 'March', 'April', 'May', 'June',
                       'July', 'August', 'September', 'October', 'November', 'December'];

    let currentDate = null;
    const elements = document.body.querySelectorAll('*');

    for (const element of elements) {
        const text = (element.textContent || '').trim();

        // Check for date headers
        const dateMatch = text.match(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\\s+(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)\\s+(\\d{4})$/i);
        if (dateMatch && element.children.length === 0) {
            const day = parseInt(dateMatch[2]);
            const month = monthNames.indexOf(dateMatch[3]) + 1;
            const year = parseInt(dateMatch[4]);
            currentDate = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
            continue;
        }
    }

    // Now find all reservation panels
    const panels = document.querySelectorAll('.as-event-panel');

    for (let i = 0; i < panels.length; i++) {
        const panel = panels[i];
        const text = panel.innerText || '';

        if (!text.includes('Reservation')) continue;

        // Extract time
        const timeMatch = text.match(/(\\d{2}):(\\d{2})\\s*-\\s*(\\d{2}):(\\d{2})/);
        if (!timeMatch) continue;

        // Extract room
        const roomMatch = text.match(/B[01]\\.[0-9]{2}/);
        if (!roomMatch) continue;

        // Find date for this panel
        let el = panel;
        let panelDate = null;
        while (el) {
            let sibling = el.previousElementSibling;
            while (sibling) {
                const sibText = (sibling.textContent || '').trim();
                const sibDateMatch = sibText.match(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\\s+(\\d{1,2})\\s+(January|February|March|April|May|June|July|August|September|October|November|December)\\s+(\\d{4})$/i);
                if (sibDateMatch) {
                    const day = parseInt(sibDateMatch[2]);
                    const month = monthNames.indexOf(sibDateMatch[3]) + 1;
                    const year = parseInt(sibDateMatch[4]);
                    panelDate = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
                    break;
                }
                sibling = sibling.previousElementSibling;
            }
            if (panelDate) break;
            el = el.parentElement;
        }

        // Check if cancelled
        let isCancelled = false;
        const allElements = panel.querySelectorAll('*');
        for (const el of allElements) {
            const style = window.getComputedStyle(el);
            if (style.textDecoration.includes('line-through') ||
                style.textDecorationLine.includes('line-through')) {
                isCancelled = true;
                break;
            }
        }

        if (!isCancelled && panelDate) {
            reservations.push({
                index: i,
                date: panelDate,
                time: timeMatch[1] + ':' + timeMatch[2],
                endTime: timeMatch[3] + ':' + timeMatch[4],
                room: roomMatch[0]
            });
        }
    }

    return reservations;
}"""


def run_real_site_test():
    """Test against the real Asimut website."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: Playwright not installed. Run: pip install playwright")
        return False

    # Check for browser state
    state_file = Path("data/browser_state/state.json")
    if not state_file.exists():
        print("ERROR: No saved browser state found at data/browser_state/state.json")
        print("Run 'python book_week.py' first to log in and save your session.")
        return False

    print("="*60)
    print("REAL SITE TEST: Date Matching on Asimut")
    print("="*60)

    with sync_playwright() as p:
        # Launch browser with saved state
        print("\nLaunching browser with saved session...")
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(storage_state=str(state_file))
        page = context.new_page()

        # Navigate to agenda
        print("Navigating to Asimut agenda...")
        page.goto("https://rwcmd.asimut.net/agenda", wait_until="networkidle")
        page.wait_for_timeout(3000)

        # Check if logged in
        if "login" in page.url.lower() or "microsoft" in page.url.lower():
            print("\nERROR: Not logged in. Session may have expired.")
            print("Run 'python book_week.py' to refresh your login.")
            browser.close()
            return False

        print("Successfully loaded agenda page")

        # Scroll to load more content
        print("\nScrolling to load all events...")
        for _ in range(10):
            page.evaluate("window.scrollBy(0, 1000)")
            page.wait_for_timeout(500)

        # Scroll back to top
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(1000)

        # Extract all reservations
        print("\nExtracting reservations from agenda...")
        reservations = page.evaluate(EXTRACT_RESERVATIONS_JS)

        if not reservations:
            print("\nNo reservations found on your agenda.")
            print("This test requires at least one existing reservation.")
            print("\nThe date matching fix has been applied but cannot be verified without bookings.")
            browser.close()
            return True  # Not a failure, just no data to test

        print(f"\nFound {len(reservations)} reservation(s):")
        for r in reservations:
            print(f"  Panel #{r['index']}: {r['room']} on {r['date']} at {r['time']}-{r['endTime']}")

        # Test the find_matching_panel function for each reservation
        print("\n" + "-"*60)
        print("Testing find_matching_panel for each reservation...")
        print("-"*60)

        all_passed = True
        for r in reservations:
            print(f"\nTest: Find {r['room']} at {r['time']} on {r['date']}")

            result = page.evaluate(FIND_MATCHING_PANEL_JS, {
                "room": r['room'],
                "time": r['time'],
                "targetDate": r['date']
            })

            if result == r['index']:
                print(f"  PASS: Found correct panel #{result}")
            elif result >= 0:
                print(f"  FAIL: Found panel #{result}, expected #{r['index']}")
                all_passed = False
            else:
                print(f"  FAIL: Panel not found (returned -1)")
                all_passed = False

        # Test with a wrong date to make sure it correctly rejects
        if reservations:
            r = reservations[0]
            # Use a date 10 days in the future (unlikely to exist)
            fake_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
            print(f"\nTest: Find {r['room']} at {r['time']} on {fake_date} (should NOT find)")

            result = page.evaluate(FIND_MATCHING_PANEL_JS, {
                "room": r['room'],
                "time": r['time'],
                "targetDate": fake_date
            })

            if result == -1:
                print(f"  PASS: Correctly returned -1 (not found)")
            else:
                print(f"  FAIL: Should return -1, got {result}")
                all_passed = False

        print("\n" + "="*60)
        if all_passed:
            print("ALL TESTS PASSED!")
        else:
            print("SOME TESTS FAILED")
        print("="*60)

        # Keep browser open briefly to see results
        print("\nClosing browser in 3 seconds...")
        page.wait_for_timeout(3000)
        browser.close()

        return all_passed


def main():
    print("="*60)
    print("Testing find_matching_panel on REAL Asimut site")
    print("="*60)
    print("\nThis test will:")
    print("  1. Open the real Asimut agenda using your saved login")
    print("  2. Find all your existing reservations")
    print("  3. Test that the date matching correctly identifies each one")
    print("  4. Test that wrong dates are correctly rejected")
    print()

    result = run_real_site_test()

    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())
