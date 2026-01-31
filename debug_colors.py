"""Debug to understand color coding of events."""

from pathlib import Path
from playwright.sync_api import sync_playwright

state_file = Path("data/browser_state/state.json")

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

    print("\n=== COLOR ANALYSIS ===\n")

    colors = page.evaluate("""() => {
        const events = document.querySelectorAll('.location-event');
        const colorMap = {};

        events.forEach(ev => {
            const style = ev.getAttribute('style') || '';
            const bgMatch = style.match(/background-color:\\s*([^;]+)/);
            const bg = bgMatch ? bgMatch[1].trim() : 'unknown';
            const text = ev.textContent.trim().substring(0, 15) || '(empty)';
            const isReservation = ev.textContent.includes('Reservation');

            const key = bg;
            if (!colorMap[key]) {
                colorMap[key] = { count: 0, reservations: 0, empty: 0, samples: [] };
            }
            colorMap[key].count++;
            if (isReservation) colorMap[key].reservations++;
            else colorMap[key].empty++;
            if (colorMap[key].samples.length < 2) {
                colorMap[key].samples.push(text);
            }
        });

        return colorMap;
    }""")

    print("Events by background color:")
    for color, data in colors.items():
        print(f"\n  {color}:")
        print(f"    Total: {data['count']}, Reservations: {data['reservations']}, Empty: {data['empty']}")
        print(f"    Samples: {data['samples']}")

    # Now check if cursor-pointer indicates clickable (bookable)
    print("\n=== CURSOR ANALYSIS ===\n")

    cursor_info = page.evaluate("""() => {
        const events = document.querySelectorAll('.location-event');
        const results = {
            withCursor: { total: 0, empty: 0 },
            withoutCursor: { total: 0, empty: 0 }
        };

        events.forEach(ev => {
            const hasCursor = ev.classList.contains('cursor-pointer');
            const isEmpty = !ev.textContent.includes('Reservation');

            if (hasCursor) {
                results.withCursor.total++;
                if (isEmpty) results.withCursor.empty++;
            } else {
                results.withoutCursor.total++;
                if (isEmpty) results.withoutCursor.empty++;
            }
        });

        return results;
    }""")

    print(f"With cursor-pointer: {cursor_info['withCursor']['total']} total, {cursor_info['withCursor']['empty']} empty")
    print(f"Without cursor-pointer: {cursor_info['withoutCursor']['total']} total, {cursor_info['withoutCursor']['empty']} empty")

    # Find truly available slots (cursor-pointer + empty text + not red background)
    print("\n=== TRULY AVAILABLE SLOTS ===\n")

    available = page.evaluate("""() => {
        const events = document.querySelectorAll('.location-event.cursor-pointer');
        const rows = document.querySelectorAll('.location-name-container .location-row');

        // Get room positions
        const roomPositions = Array.from(rows).map(row => {
            const rect = row.getBoundingClientRect();
            return {
                name: row.textContent.trim(),
                midY: (rect.top + rect.bottom) / 2
            };
        });

        const available = [];
        events.forEach(ev => {
            const text = ev.textContent.trim();
            if (text.includes('Reservation')) return;  // Skip booked

            const style = ev.getAttribute('style') || '';
            // Check if red (blocked)
            if (style.includes('rgb(246, 60') || style.includes('rgb(205, 92')) return;

            // Find room by Y position
            const rect = ev.getBoundingClientRect();
            const evMidY = (rect.top + rect.bottom) / 2;

            let closestRoom = 'unknown';
            let closestDist = Infinity;
            roomPositions.forEach(room => {
                const dist = Math.abs(room.midY - evMidY);
                if (dist < closestDist) {
                    closestDist = dist;
                    closestRoom = room.name;
                }
            });

            // Extract time from left position
            const leftMatch = style.match(/left:\\s*calc\\(([\d.]+)%/);
            const widthMatch = style.match(/width:\\s*calc\\(([\d.]+)%/);
            const leftPct = leftMatch ? parseFloat(leftMatch[1]) : 0;
            const widthPct = widthMatch ? parseFloat(widthMatch[1]) : 6.25;

            // Convert to time (assuming 0% = 08:00, 6.25% per hour)
            const startHour = 8 + Math.round(leftPct / 6.25);
            const durationHours = Math.round(widthPct / 6.25);

            available.push({
                room: closestRoom,
                startTime: `${startHour.toString().padStart(2, '0')}:00`,
                endTime: `${(startHour + durationHours).toString().padStart(2, '0')}:00`,
                leftPct,
                widthPct
            });
        });

        return available;
    }""")

    print(f"Found {len(available)} truly available slots:")
    for slot in available[:20]:
        print(f"  {slot['room']}: {slot['startTime']}-{slot['endTime']} (left={slot['leftPct']:.1f}%, width={slot['widthPct']:.1f}%)")

    print("\n\nKeeping browser open for 30 seconds...")
    page.wait_for_timeout(30000)

    context.close()
    browser.close()
