"""Debug script to capture Asimut page structure."""

from pathlib import Path
from playwright.sync_api import sync_playwright

# Load saved browser state
state_file = Path("data/browser_state/state.json")
screenshots_dir = Path("debug_screenshots")
screenshots_dir.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    # Use a larger viewport
    context = browser.new_context(
        storage_state=str(state_file),
        viewport={"width": 1920, "height": 1080}
    )
    page = context.new_page()

    # Go to Asimut
    print("Navigating to Asimut...")
    page.goto("https://rwcmd.asimut.net/", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2000)

    # Take initial screenshot
    page.screenshot(path=str(screenshots_dir / "01_main.png"))
    print("Screenshot saved: 01_main.png")

    # Try to find and click Locations - use visible locator
    print("Looking for Locations link...")
    locations = page.locator("text=Locations").all()
    print(f"Found {len(locations)} 'Locations' elements")

    for i, loc in enumerate(locations):
        try:
            is_visible = loc.is_visible()
            print(f"  [{i}] visible: {is_visible}")
            if is_visible:
                print(f"  Clicking element {i}...")
                loc.click()
                page.wait_for_timeout(2000)
                break
        except Exception as e:
            print(f"  [{i}] error: {e}")

    # Take screenshot after clicking Locations
    page.screenshot(path=str(screenshots_dir / "02_after_locations.png"))
    print("Screenshot saved: 02_after_locations.png")

    # Save HTML to see structure
    html = page.content()
    (screenshots_dir / "02_page.html").write_text(html, encoding="utf-8")

    # Look for Music Practice Rooms - AHC
    print("\nLooking for 'Music Practice Rooms - AHC'...")
    ahc_elements = page.locator("text=Music Practice Rooms - AHC").all()
    print(f"Found {len(ahc_elements)} elements")

    for i, el in enumerate(ahc_elements):
        try:
            is_visible = el.is_visible()
            print(f"  [{i}] visible: {is_visible}")
            if is_visible:
                el.click()
                page.wait_for_timeout(3000)
                break
        except Exception as e:
            print(f"  [{i}] error: {e}")

    # Take screenshot of practice rooms
    page.screenshot(path=str(screenshots_dir / "03_practice_rooms.png"))
    print("Screenshot saved: 03_practice_rooms.png")

    # Save final HTML
    html = page.content()
    (screenshots_dir / "03_practice_rooms.html").write_text(html, encoding="utf-8")
    print("HTML saved: 03_practice_rooms.html")

    print("\nKeeping browser open for 60 seconds for manual inspection...")
    print("Please manually navigate if needed and note the steps.")
    page.wait_for_timeout(60000)

    context.close()
    browser.close()
