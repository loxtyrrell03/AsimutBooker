"""Isolated phone and withdrawn desktop closure rendering checks; no live writes."""
import argparse
import inspect
import json
import mimetypes
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from datetime import date
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assistant_context import ContextPaths
from phone_api import build_phone_snapshot
from phone_preferences import read_phone_preferences
from gui import AsimutBookerGUI
from playwright.sync_api import sync_playwright, expect


def check(dist):
    output = dist.parent / 'calendar-crossed-ui'
    output.mkdir(exist_ok=True)
    root = tk.Tk()
    root.withdraw()
    try:
        app = object.__new__(AsimutBookerGUI)
        app.calendar_frame = tk.Frame(root)
        app.calendar_closed_dates = {'2026-09-09'}
        app.disabled_dates = {'2026-09-10'}
        app.calendar_events = {}
        app.calendar_plan_result = None
        app.booking_dates = ()
        app._ensure_calendar_day_var = lambda day, **kwargs: tk.BooleanVar(value=day.isoformat() != '2026-09-10')
        app._render_day_cell(0, 0, date(2026, 9, 9), '2026-09-09', date(2026, 9, 8))
        app._render_day_cell(0, 1, date(2026, 9, 10), '2026-09-10', date(2026, 9, 8))
        closed_cell, off_cell = app.calendar_frame.winfo_children()
        closed_heading = closed_cell.winfo_children()[0]
        off_heading = off_cell.winfo_children()[0]
        assert closed_cell.cget('bg') == '#fee2e2'
        assert closed_heading.cget('fg') == '#b91c1c'
        assert 'overstrike' in closed_heading.cget('font')
        assert off_heading.cget('fg') != '#b91c1c'
        assert 'overstrike' in off_heading.cget('font')
    finally:
        root.destroy()
    print('PASS desktop: booking-off date is crossed out; closed date remains red and crossed out')

    origin = json.loads((dist / 'build-info.json').read_text())['public_origin']
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
        paths = ContextPaths(**{key: Path(temporary) / key for key in inspect.signature(ContextPaths).parameters})
        paths.settings.write_text(json.dumps({'disabled_dates': ['2026-09-10']}), encoding='utf-8')
        booker = build_phone_snapshot(paths=paths)
        booker['agenda'].update(available=True, stale=False, closed_dates=['2026-09-09'], events=[{
            'date': '2026-09-10', 'start_time': '12:00', 'end_time': '13:00',
            'room': 'Test practice room', 'title': 'Reservation', 'is_reservation': True,
        }])
        state = {'busy': False, 'messages': [], 'event_cursor': 0, 'stream_generation': 'closures-test',
                 'booker': booker, 'unresolved_reserved_count': 0}
        for engine in ('chromium', 'webkit'):
            browser = getattr(playwright, engine).launch(headless=True)
            page = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True,
                                    has_touch=True, service_workers='block')
            page.add_init_script('window.EventSource = class { addEventListener() {} close() {} };')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def intercept(route):
                path = urlsplit(route.request.url).path
                if path == '/api/v1/preferences':
                    body = read_phone_preferences(paths.settings)
                    route.fulfill(content_type='application/json', body=json.dumps(body))
                elif path.startswith('/api/'):
                    body = {'csrf_token': 'test', 'bootstrap': state} if path.endswith('/session') else state
                    route.fulfill(content_type='application/json', body=json.dumps(body))
                else:
                    asset = dist / (path.lstrip('/') or 'index.html')
                    route.fulfill(path=asset, content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')

            page.route('**/*', intercept)
            page.goto(origin)
            page.get_by_role('button', name='My Week', exact=True).last.click()
            closed = page.locator('.day-section.rooms-closed')
            expect(closed).to_have_count(1)
            expect(closed.locator('h3')).to_have_css('text-decoration-line', 'line-through')
            expect(closed.locator('h3')).to_have_css('color', 'rgb(185, 28, 28)')
            expect(closed.locator('.closure-label')).to_have_text('Practice rooms closed')
            expect(page.locator('.day-section:not(.rooms-closed) .agenda-card')).to_have_count(1)
            page.get_by_role('button', name='Calendar', exact=True).last.click()
            off = page.get_by_role('button', name='Thursday 10 September, booking off', exact=True)
            expect(off.locator('span')).to_have_css('text-decoration-line', 'line-through')
            expect(page.locator('h3.booking-off-key').filter(has_text='Thursday 10 September')).to_have_css(
                'text-decoration-line', 'line-through'
            )
            closed_day = page.get_by_role(
                'button', name='Wednesday 9 September, booking on, practice rooms closed', exact=True
            )
            expect(closed_day.locator('span')).to_have_css('text-decoration-line', 'line-through')
            expect(closed_day.locator('span')).to_have_css('color', 'rgb(183, 51, 50)')
            page.screenshot(path=str(output / f'{engine}-calendar.png'), full_page=True)
            assert not errors, errors
            browser.close()
            print(f'PASS {engine}: booking-off calendar dates are crossed out; room closures remain red')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True)
    check(parser.parse_args().dist.resolve())
