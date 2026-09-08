"""Read-only grid diagnostics. Never calls a booking/extension Save path."""

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import book_week as b
from app_settings import load_settings
from playwright.sync_api import sync_playwright
from room_preferences import load_room_preferences
from runtime_guard import SingleInstanceLock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--open-forms', action='store_true', help='Open one unsaved form per room; block every Save request')
    args = parser.parse_args()
    lock = SingleInstanceLock(b.APP_DIR / 'data/booker-runtime.lock')
    for _ in range(90):
        if lock.acquire():
            break
        time.sleep(2)
    else:
        raise RuntimeError('Booker runtime remained busy')
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(**b.authenticated_runtime_context_options())
            # Defense in depth: this diagnostic may inspect/open unsaved forms,
            # but must never persist a remote event.
            blocked_saves = []
            def block_save(route):
                blocked_saves.append(True)
                route.abort()
            context.route('**/event/type=save*', block_save)
            page = context.new_page()
            b.restore_page_authentication(page)
            b.refresh_live_room_policy(page, load_room_preferences(load_settings()), today=date.today())
            b.open_practice_room_overview(page, date.today())
            if args.open_forms:
                target_date = date.today() + timedelta(days=1)
                b.navigate_to_day(page, 1, 0, base_date=date.today())
                results = []
                for room in b.PRIORITY_ROOMS:
                    available = b.get_available_slots(page)
                    gaps = next(row['slots'] for row in available if row['room'] == room)
                    gap = next((gap for gap in gaps if gap['endHour']-gap['startHour'] >= 0.5), None)
                    if gap is None:
                        results.append({'room':room, 'result':'no free half-hour visible'})
                        continue
                    start = gap['startHour']
                    coords = b.get_room_slot_coordinates(page, room, start, start+0.5)
                    if coords is None:
                        raise RuntimeError(f'No safe visible target for {room}')
                    page.mouse.click(coords['x'],coords['y'])
                    option = b.wait_for_student_booking_option(page, timeout_ms=5000)
                    if option is not None:
                        option.click()
                    if not b.wait_for_new_booking_form(page):
                        raise RuntimeError(f'Unsaved form did not open for {room}')
                    snapshot = b.page_booking_snapshot(page)
                    if snapshot.get('room') != room or snapshot.get('date') != target_date.isoformat():
                        raise RuntimeError(f'Unsaved form identity mismatch for {room}: {snapshot}')
                    results.append({'room':room, 'result':'exact unsaved form opened'})
                    print(json.dumps(results[-1]), flush=True)
                    b.open_practice_room_overview(page,date.today())
                    b.navigate_to_day(page,1,0,base_date=date.today())
                print(json.dumps({'checked':len(results),'opened':sum(row['result']=='exact unsaved form opened' for row in results),'save_requests_blocked':len(blocked_saves)}))
            if blocked_saves:
                raise RuntimeError('Unexpected Save request blocked during read-only diagnostics')
            browser.close()
    finally:
        lock.release()


if __name__ == '__main__':
    main()
