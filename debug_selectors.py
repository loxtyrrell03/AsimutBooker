"""Debug script to understand the grid structure."""

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

    print("\n=== EXPLORING PAGE STRUCTURE ===\n")

    # Find all elements with class containing 'location'
    classes = page.evaluate("""() => {
        const elements = document.querySelectorAll('[class*="location"]');
        const classSet = new Set();
        elements.forEach(el => {
            el.className.split(' ').forEach(c => {
                if (c.includes('location')) classSet.add(c);
            });
        });
        return Array.from(classSet);
    }""")
    print(f"Classes containing 'location': {classes}")

    # Find all elements with class containing 'event'
    event_classes = page.evaluate("""() => {
        const elements = document.querySelectorAll('[class*="event"]');
        const classSet = new Set();
        elements.forEach(el => {
            el.className.split(' ').forEach(c => {
                if (c.includes('event')) classSet.add(c);
            });
        });
        return Array.from(classSet);
    }""")
    print(f"Classes containing 'event': {event_classes}")

    # Find all divs that could be containers
    containers = page.evaluate("""() => {
        const allDivs = document.querySelectorAll('div[class]');
        const containers = [];
        allDivs.forEach(div => {
            const events = div.querySelectorAll('.location-event');
            if (events.length > 10) {
                containers.push({
                    class: div.className,
                    eventCount: events.length
                });
            }
        });
        return containers;
    }""")
    print(f"\nContainers with >10 location-event children:")
    for c in containers:
        print(f"  {c['class']}: {c['eventCount']} events")

    # Find the parent of location-event elements
    print("\n=== LOCATION-EVENT PARENT STRUCTURE ===")
    parent_info = page.evaluate("""() => {
        const events = document.querySelectorAll('.location-event');
        if (events.length === 0) return 'No events found';

        const first = events[0];
        let parent = first.parentElement;
        const parents = [];
        while (parent && parents.length < 5) {
            parents.push({
                tag: parent.tagName,
                class: parent.className?.substring(0, 80) || '',
                childCount: parent.children.length
            });
            parent = parent.parentElement;
        }
        return parents;
    }""")
    print("Parent chain of first location-event:")
    for i, p in enumerate(parent_info):
        print(f"  [{i}] <{p['tag']}> class='{p['class']}' children={p['childCount']}")

    # Now let's find how events are organized by room
    print("\n=== EVENT ORGANIZATION ===")
    org = page.evaluate("""() => {
        const events = document.querySelectorAll('.location-event');
        // Group by parent
        const parentMap = new Map();
        events.forEach(ev => {
            const parent = ev.parentElement;
            const key = parent.className;
            if (!parentMap.has(key)) {
                parentMap.set(key, { count: 0, sample: parent });
            }
            parentMap.get(key).count++;
        });

        return Array.from(parentMap.entries()).map(([key, val]) => ({
            parentClass: key.substring(0, 80),
            eventCount: val.count
        }));
    }""")
    for item in org:
        print(f"  Parent class '{item['parentClass']}': {item['eventCount']} events")

    # Let's look at the structure differently - find row containers
    print("\n=== CHECKING LOCATION-EVENTS-ROW ===")
    row_check = page.evaluate("""() => {
        // Check for location-events-row (plural)
        const rows = document.querySelectorAll('.location-events-row');
        if (rows.length > 0) {
            return {
                found: true,
                count: rows.length,
                sample: {
                    class: rows[0].className,
                    childCount: rows[0].children.length,
                    eventCount: rows[0].querySelectorAll('.location-event').length
                }
            };
        }

        // Check for events-row
        const evRows = document.querySelectorAll('[class*="events-row"]');
        if (evRows.length > 0) {
            return {
                found: true,
                selector: 'events-row',
                count: evRows.length
            };
        }

        return { found: false };
    }""")
    print(f"Location-events-row check: {row_check}")

    # Let's try a different approach - find all rows that have events
    print("\n=== FINDING ROW/EVENT CORRELATION ===")
    correlation = page.evaluate("""() => {
        const nameRows = document.querySelectorAll('.location-name-container .location-row');

        // For each name row, try to find corresponding events
        // by checking for sibling structure or matching data attributes
        const results = [];

        nameRows.forEach((nameRow, idx) => {
            const roomName = nameRow.textContent.trim();

            // Check if nameRow has a data attribute
            const dataAttr = nameRow.getAttribute('data-location-id') ||
                            nameRow.getAttribute('data-id') ||
                            nameRow.getAttribute('id');

            results.push({
                index: idx,
                room: roomName,
                dataAttr: dataAttr
            });
        });

        return results.slice(0, 5);
    }""")
    print("Name rows (first 5):")
    for r in correlation:
        print(f"  [{r['index']}] {r['room']} - data: {r['dataAttr']}")

    print("\n\nKeeping browser open for 30 seconds...")
    page.wait_for_timeout(30000)

    context.close()
    browser.close()
