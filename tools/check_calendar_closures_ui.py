"""Phone closure checks using recorded Asimut data and isolated API interception."""
import argparse
import inspect
import json
import mimetypes
from pathlib import Path
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assistant_context import ContextPaths
from phone_api import build_phone_snapshot
from phone_preferences import read_phone_preferences
from room_catalog import save_catalog, with_closure_events
from tests.test_room_catalog import ClosureCalendarTests
from playwright.sync_api import sync_playwright, expect


def check(dist):
    output = dist.parent / 'calendar-crossed-ui'
    output.mkdir(exist_ok=True)
    origin = json.loads((dist / 'build-info.json').read_text())['public_origin']
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
        paths = ContextPaths(**{key: Path(temporary) / key for key in inspect.signature(ContextPaths).parameters})
        paths.settings.write_text(json.dumps({'disabled_dates': ['2026-09-14']}), encoding='utf-8')
        fixture, catalog = ClosureCalendarTests().real_weekend()
        save_catalog(with_closure_events(catalog, fixture['agenda'], fixture['categories']), paths.catalog)
        with patch('room_catalog.datetime', wraps=datetime) as clock:
            clock.now.return_value = catalog.observed_at
            booker = build_phone_snapshot(paths=paths)
        assert booker['agenda']['closed_dates'] == ['2026-09-12', '2026-09-13']
        booker['agenda'].update(available=True, stale=False, events=[{
            'event_id': 123, 'date': '2026-09-12', 'start_time': '12:00', 'end_time': '13:00',
            'room': 'Example retained booking', 'title': 'Reservation', 'is_reservation': True,
        }])
        state = {'busy': False, 'messages': [], 'event_cursor': 0, 'stream_generation': 'closures-test',
                 'booker': booker, 'unresolved_reserved_count': 0}
        for engine in ('chromium', 'webkit'):
            browser = getattr(playwright, engine).launch(headless=True)
            page = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True,
                                    has_touch=True, service_workers='block')
            page.clock.install(time=catalog.observed_at)
            page.add_init_script('window.EventSource = class { addEventListener() {} close() {} };')
            errors, writes = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def intercept(route):
                path = urlsplit(route.request.url).path
                if path == '/api/v1/preferences':
                    if route.request.method != 'GET': writes.append(path)
                    body = read_phone_preferences(paths.settings)
                    route.fulfill(content_type='application/json', body=json.dumps(body))
                elif path.startswith('/api/'):
                    if route.request.method != 'GET' and path not in ('/api/v1/session', '/api/v1/live-refresh'):
                        writes.append(path)
                    body = {'csrf_token': 'test', 'bootstrap': state} if path.endswith('/session') else state
                    route.fulfill(content_type='application/json', body=json.dumps(body))
                else:
                    asset = dist / (path.lstrip('/') or 'index.html')
                    route.fulfill(path=asset, content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')

            page.route('**/*', intercept)
            page.goto(origin)
            page.get_by_role('button', name='My Week', exact=True).last.click()
            expect(page.locator('.day-section.rooms-closed > .closure-cross')).to_have_count(2)
            expect(page.locator('.day-section.rooms-closed .agenda-card')).to_have_count(1)
            page.get_by_role('button', name='Calendar', exact=True).last.click()
            page.get_by_label('Go to date', exact=True).fill('2026-09-12')
            for width in (320, 390, 844):
                page.set_viewport_size({'width': width, 'height': 844})
                for mode in ('month', 'fortnight', 'week', '3days', 'plan'):
                    page.get_by_label('View', exact=True).select_option(mode)
                    surface = '.calendar-month > .day-closed' if mode in ('month', 'fortnight') else '.timeline-track.closed-day-surface' if mode in ('week', '3days') else '.calendar-plan.closed-day-surface'
                    closed = page.locator(surface)
                    expect(closed).to_have_count(2)
                    expect(closed.locator(':scope > .closure-cross')).to_have_count(2)
                    for mark in closed.locator(':scope > .closure-cross').all():
                        expect(mark).to_have_css('pointer-events', 'none')
                        size = mark.bounding_box()
                        parent = mark.locator('..').bounding_box()
                        assert size['width'] >= parent['width'] * .5 and size['height'] >= parent['height'] * .65
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    if mode == 'month':
                        off = page.get_by_role('button', name='Monday 14 September, booking off', exact=True)
                        expect(off.locator('.closure-cross')).to_have_count(0)
                        expect(off.locator('span')).to_have_css('text-decoration-line', 'line-through')
                    if width == 390:
                        page.locator('h2').filter(has_text='Calendar').scroll_into_view_if_needed()
                        page.screenshot(path=str(output / f'{engine}-{mode}.png'), full_page=True)
                # Closed dates survive even without any plan, and existing bookings stay interactive.
                page.locator('.calendar-plan[data-date="2026-09-12"] .calendar-event').click()
                expect(page.get_by_role('button', name='Back', exact=True)).to_be_visible()
                page.get_by_role('button', name='Back', exact=True).click()
            page.get_by_label('View', exact=True).select_option('month')
            page.get_by_role('button', name='Previous period', exact=True).click()
            page.get_by_role('button', name='Next period', exact=True).click()
            expect(page.get_by_label('Go to date', exact=True)).to_have_value('2026-09-01')
            assert not errors, errors
            assert not writes, writes
            browser.close()
            print(f'PASS {engine}: derived weekend crosses in all five modes at 320/390/844px; booking details and navigation work')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True)
    check(parser.parse_args().dist.resolve())
