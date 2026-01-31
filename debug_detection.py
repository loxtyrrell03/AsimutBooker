"""Debug what the detection code actually finds."""

from pathlib import Path
from playwright.sync_api import sync_playwright

state_file = Path("data/browser_state/state.json")

BLOCKED_COLORS = [
    "rgb(246, 60",   # Red - blocked
    "rgb(205, 92",   # Red variant
    "rgb(81, 165",   # Blue - confirmed reservation
    "rgb(46, 177",   # Blue variant
    "rgb(115, 180",  # Blue variant
    "rgb(195, 61",   # Purple variant
    "rgb(200, 200",  # Grey - pending reservation (ADDING THIS)
]

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

    # Navigate to Feb 2
    print("Navigating to Feb 2...")
    arrow = page.locator("mat-icon:text('chevron_right')").first
    arrow.click()
    page.wait_for_timeout(2000)

    # Run the exact same detection logic
    blocked_colors_js = ", ".join(f'"{c}"' for c in BLOCKED_COLORS)

    result = page.evaluate(f"""() => {{
        const BLOCKED_COLORS = [{blocked_colors_js}];

        const events = document.querySelectorAll('.location-event.cursor-pointer');
        const rows = document.querySelectorAll('.location-name-container .location-row');

        // Get room positions
        const roomPositions = Array.from(rows).map(row => {{
            const rect = row.getBoundingClientRect();
            return {{
                name: row.textContent.trim(),
                midY: (rect.top + rect.bottom) / 2
            }};
        }});

        const available = [];
        events.forEach((ev, idx) => {{
            const text = ev.textContent.trim();

            // Skip if has any text (Reservation, etc.)
            if (text.length > 0) {{
                return;  // This should filter out grey pending ones
            }}

            const style = ev.getAttribute('style') || '';

            // Check if colored
            const isBlocked = BLOCKED_COLORS.some(color => style.includes(color));

            // Find room by Y position
            const rect = ev.getBoundingClientRect();
            const evMidY = (rect.top + rect.bottom) / 2;

            let closestRoom = 'unknown';
            let closestDist = Infinity;
            roomPositions.forEach(room => {{
                const dist = Math.abs(room.midY - evMidY);
                if (dist < closestDist) {{
                    closestDist = dist;
                    closestRoom = room.name;
                }}
            }});

            // Extract time
            const leftMatch = style.match(/left:\\s*calc\\(([\\d.]+)%/);
            const leftPct = leftMatch ? parseFloat(leftMatch[1]) : 0;
            const startHour = 8 + Math.round(leftPct / 6.25);

            // Log ALL empty-text events (not just non-blocked)
            available.push({{
                room: closestRoom,
                startHour: startHour,
                leftPct: leftPct,
                isBlocked: isBlocked,
                bgColor: style.match(/background-color:\\s*([^;]+)/)?.[1] || 'none',
                evMidY: evMidY,
                closestDist: closestDist
            }});
        }});

        return {{
            totalEvents: events.length,
            emptyTextEvents: available.length,
            available: available
        }};
    }}""")

    print(f"\nTotal events: {result['totalEvents']}")
    print(f"Empty text events: {result['emptyTextEvents']}")

    print("\n=== ALL EMPTY-TEXT EVENTS ===")
    for ev in result['available']:
        blocked_str = "BLOCKED" if ev['isBlocked'] else "AVAILABLE"
        print(f"  {ev['room']} at {ev['startHour']:02d}:00 (left={ev['leftPct']:.1f}%) - {blocked_str} - bg={ev['bgColor']}")

    print("\n=== WOULD-BE AVAILABLE (not blocked) ===")
    truly_available = [ev for ev in result['available'] if not ev['isBlocked']]
    for ev in truly_available:
        print(f"  {ev['room']} at {ev['startHour']:02d}:00 (left={ev['leftPct']:.1f}%) - bg={ev['bgColor']}")

    print("\n\nKeeping browser open for 30 seconds...")
    page.wait_for_timeout(30000)

    context.close()
    browser.close()
