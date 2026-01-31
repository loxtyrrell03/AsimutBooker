"""Final debug script to understand positioning."""

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

    # Click Locations
    for loc in page.locator("text=Locations").all():
        if loc.is_visible():
            loc.click()
            page.wait_for_timeout(2000)
            break

    # Click AHC rooms
    for loc in page.locator("text=Music Practice Rooms - AHC").all():
        if loc.is_visible():
            loc.click()
            page.wait_for_timeout(3000)
            break

    print("\n=== UNDERSTANDING EVENT POSITIONING ===\n")

    # Get info about location-day containers and their events
    day_info = page.evaluate("""() => {
        const days = document.querySelectorAll('.location-day');
        return Array.from(days).slice(0, 3).map((day, dayIdx) => {
            const events = day.querySelectorAll('.location-event');
            return {
                dayIndex: dayIdx,
                eventCount: events.length,
                dayStyle: day.getAttribute('style')?.substring(0, 100),
                events: Array.from(events).slice(0, 5).map(ev => ({
                    text: ev.textContent.substring(0, 20).trim(),
                    style: ev.getAttribute('style'),
                    top: ev.style.top || ev.getAttribute('style')?.match(/top:\s*([^;]+)/)?.[1]
                }))
            };
        });
    }""")

    print("Location-day containers (first 3):")
    for day in day_info:
        print(f"\n  Day {day['dayIndex']}: {day['eventCount']} events")
        print(f"    Style: {day['dayStyle']}")
        for ev in day['events']:
            print(f"      Event: '{ev['text']}' | style: {ev['style'][:80] if ev['style'] else 'none'}")

    # Check how events are positioned vertically
    print("\n=== CHECKING VERTICAL POSITIONING ===\n")

    vert_pos = page.evaluate("""() => {
        const events = document.querySelectorAll('.location-event');
        const positions = [];

        events.forEach(ev => {
            const rect = ev.getBoundingClientRect();
            const text = ev.textContent.substring(0, 20).trim();
            positions.push({
                text: text,
                top: Math.round(rect.top),
                bottom: Math.round(rect.bottom),
                left: Math.round(rect.left),
                height: Math.round(rect.height)
            });
        });

        // Sort by top position
        positions.sort((a, b) => a.top - b.top);

        return positions.slice(0, 20);
    }""")

    print("Events by vertical position (first 20):")
    for ev in vert_pos:
        print(f"  top={ev['top']} height={ev['height']} left={ev['left']} | '{ev['text']}'")

    # Get room row positions
    print("\n=== ROOM ROW POSITIONS ===\n")

    room_pos = page.evaluate("""() => {
        const rows = document.querySelectorAll('.location-name-container .location-row');
        return Array.from(rows).map((row, idx) => {
            const rect = row.getBoundingClientRect();
            const name = row.textContent.trim();
            return {
                index: idx,
                room: name,
                top: Math.round(rect.top),
                bottom: Math.round(rect.bottom),
                height: Math.round(rect.height)
            };
        });
    }""")

    print("Room row positions:")
    for r in room_pos[:15]:
        print(f"  [{r['index']}] {r['room']}: top={r['top']} height={r['height']}")

    # Now correlate events to rooms by Y position
    print("\n=== CORRELATING EVENTS TO ROOMS ===\n")

    correlation = page.evaluate("""() => {
        const rows = document.querySelectorAll('.location-name-container .location-row');
        const events = document.querySelectorAll('.location-event');

        // Get room positions
        const roomPositions = Array.from(rows).map((row, idx) => {
            const rect = row.getBoundingClientRect();
            return {
                name: row.textContent.trim(),
                top: rect.top,
                bottom: rect.bottom,
                midY: (rect.top + rect.bottom) / 2
            };
        });

        // For each event, find which room it belongs to by Y position
        const eventsByRoom = {};
        events.forEach(ev => {
            const rect = ev.getBoundingClientRect();
            const evMidY = (rect.top + rect.bottom) / 2;

            // Find the room with closest midY
            let closestRoom = null;
            let closestDist = Infinity;
            roomPositions.forEach(room => {
                const dist = Math.abs(room.midY - evMidY);
                if (dist < closestDist) {
                    closestDist = dist;
                    closestRoom = room.name;
                }
            });

            if (closestRoom) {
                if (!eventsByRoom[closestRoom]) {
                    eventsByRoom[closestRoom] = { total: 0, available: 0 };
                }
                eventsByRoom[closestRoom].total++;
                if (!ev.textContent.includes('Reservation')) {
                    eventsByRoom[closestRoom].available++;
                }
            }
        });

        return eventsByRoom;
    }""")

    print("Events by room (matched by Y position):")
    for room, counts in correlation.items():
        if counts['available'] > 0:
            print(f"  {room}: {counts['available']} available / {counts['total']} total")

    print("\n\nKeeping browser open for 30 seconds...")
    page.wait_for_timeout(30000)

    context.close()
    browser.close()
