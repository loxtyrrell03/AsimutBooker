"""Debug script to analyze the booking grid structure."""

from pathlib import Path
from playwright.sync_api import sync_playwright

state_file = Path("data/browser_state/state.json")
screenshots_dir = Path("debug_screenshots")
screenshots_dir.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        storage_state=str(state_file),
        viewport={"width": 1920, "height": 1080}
    )
    page = context.new_page()

    # Navigate to practice rooms
    print("Navigating to Asimut...")
    page.goto("https://rwcmd.asimut.net/", wait_until="networkidle", timeout=60000)
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

    # Take screenshot
    page.screenshot(path=str(screenshots_dir / "grid_analysis.png"))

    # Now let's analyze the grid structure
    print("\n=== ANALYZING GRID STRUCTURE ===\n")

    # Find room B0.35 (which appears empty in the screenshot)
    room = "B0.35"
    print(f"Looking for room: {room}")

    room_els = page.locator(f"text='{room}'").all()
    print(f"Found {len(room_els)} elements with text '{room}'")

    for i, el in enumerate(room_els):
        try:
            print(f"\n--- Element {i} ---")
            print(f"  Visible: {el.is_visible()}")
            print(f"  Tag: {el.evaluate('el => el.tagName')}")
            print(f"  Class: {el.get_attribute('class')}")

            # Get parent structure
            parent = el.locator("xpath=..")
            if parent.count() > 0:
                print(f"  Parent tag: {parent.evaluate('el => el.tagName')}")
                print(f"  Parent class: {parent.get_attribute('class')}")

            # Try to find siblings (the cells in the row)
            siblings = el.locator("xpath=following-sibling::*").all()
            print(f"  Following siblings: {len(siblings)}")

            # Get grandparent (likely the row container)
            grandparent = el.locator("xpath=../..")
            if grandparent.count() > 0:
                gp_tag = grandparent.evaluate("el => el.tagName")
                gp_class = grandparent.get_attribute("class") or ""
                print(f"  Grandparent tag: {gp_tag}")
                print(f"  Grandparent class: {gp_class[:100]}")

                # Count children of grandparent
                gp_children = grandparent.locator("> *").all()
                print(f"  Grandparent children: {len(gp_children)}")

        except Exception as e:
            print(f"  Error: {e}")

    # Let's look at the overall grid structure
    print("\n=== LOOKING FOR GRID ELEMENTS ===\n")

    grid_selectors = [
        "[class*='grid']",
        "[class*='schedule']",
        "[class*='calendar']",
        "[class*='timeline']",
        "[class*='resource']",
        "table",
        "[class*='row']",
    ]

    for selector in grid_selectors:
        elements = page.locator(selector).all()
        if elements:
            print(f"'{selector}': {len(elements)} elements")
            if len(elements) <= 5:
                for el in elements:
                    try:
                        tag = el.evaluate("el => el.tagName")
                        cls = (el.get_attribute("class") or "")[:80]
                        print(f"    <{tag}> class='{cls}'")
                    except:
                        pass

    # Save full HTML for detailed analysis
    html = page.content()
    (screenshots_dir / "grid_full.html").write_text(html, encoding="utf-8")
    print("\nFull HTML saved to grid_full.html")

    # Look for clickable/interactive elements in the grid area
    print("\n=== LOOKING FOR BOOKING CELLS ===\n")

    # Find elements that might be bookable cells (usually divs with click handlers)
    potential_cells = page.locator("[class*='event'], [class*='cell'], [class*='slot'], [class*='block']").all()
    print(f"Found {len(potential_cells)} potential cell elements")

    # Sample a few
    for i, cell in enumerate(potential_cells[:10]):
        try:
            cls = cell.get_attribute("class") or ""
            style = (cell.get_attribute("style") or "")[:50]
            text = (cell.text_content() or "")[:30]
            print(f"  [{i}] class='{cls[:60]}' style='{style}' text='{text}'")
        except Exception as e:
            print(f"  [{i}] error: {e}")

    print("\nKeeping browser open for 60 seconds...")
    page.wait_for_timeout(60000)

    context.close()
    browser.close()
