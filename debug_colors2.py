"""Debug to check what slots are being detected on Feb 2."""

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

    # Navigate to Feb 2
    print("Navigating to Feb 2...")
    arrow = page.locator("mat-icon:text('chevron_right')").first
    arrow.click()
    page.wait_for_timeout(2000)

    # Take screenshot
    page.screenshot(path="debug_screenshots/feb2.png")

    # Check what the B1.16 row looks like
    print("\n=== CHECKING B1.16 ROW ===\n")

    result = page.evaluate("""() => {
        const rows = document.querySelectorAll('.location-name-container .location-row');
        const events = document.querySelectorAll('.location-event.cursor-pointer');

        // Find B1.16 position
        let b116MidY = null;
        rows.forEach(row => {
            if (row.textContent.trim() === 'B1.16') {
                const rect = row.getBoundingClientRect();
                b116MidY = (rect.top + rect.bottom) / 2;
            }
        });

        if (!b116MidY) return { error: 'B1.16 not found' };

        // Find all events near B1.16's Y position
        const b116Events = [];
        events.forEach((ev, idx) => {
            const rect = ev.getBoundingClientRect();
            const evMidY = (rect.top + rect.bottom) / 2;

            if (Math.abs(evMidY - b116MidY) < 25) {  // Within 25px
                const style = ev.getAttribute('style') || '';
                const bgMatch = style.match(/background-color:\\s*([^;]+)/);

                b116Events.push({
                    index: idx,
                    text: ev.textContent.trim().substring(0, 20),
                    backgroundColor: bgMatch ? bgMatch[1] : 'none',
                    style: style.substring(0, 150)
                });
            }
        });

        return {
            b116MidY,
            eventCount: b116Events.length,
            events: b116Events
        };
    }""")

    print(f"B1.16 Y position: {result.get('b116MidY')}")
    print(f"Events in B1.16 row: {result.get('eventCount')}")
    for ev in result.get('events', []):
        print(f"\n  [{ev['index']}] text='{ev['text']}'")
        print(f"      bg={ev['backgroundColor']}")
        print(f"      style={ev['style']}")

    # Check what the 18:00 slot looks like specifically
    print("\n=== CHECKING 18:00 AREA ===\n")

    # 18:00 should be at left position around 62.5% (10 hours from 08:00, each hour is 6.25%)
    check_18 = page.evaluate("""() => {
        const events = document.querySelectorAll('.location-event.cursor-pointer');
        const near18 = [];

        events.forEach((ev, idx) => {
            const style = ev.getAttribute('style') || '';
            const leftMatch = style.match(/left:\\s*calc\\(([\\d.]+)%/);
            if (!leftMatch) return;

            const leftPct = parseFloat(leftMatch[1]);
            // 18:00 = 10 hours from 8:00, so leftPct should be around 62.5%
            if (leftPct >= 60 && leftPct <= 65) {
                const bgMatch = style.match(/background-color:\\s*([^;]+)/);
                near18.push({
                    index: idx,
                    text: ev.textContent.trim().substring(0, 20),
                    leftPct,
                    backgroundColor: bgMatch ? bgMatch[1] : 'none',
                });
            }
        });

        return near18;
    }""")

    print("Events at ~18:00 position (left 60-65%):")
    for ev in check_18:
        print(f"  [{ev['index']}] left={ev['leftPct']:.1f}% text='{ev['text']}' bg={ev['backgroundColor']}")

    print("\n\nKeeping browser open for 30 seconds...")
    page.wait_for_timeout(30000)

    context.close()
    browser.close()
