"""Verify planned sessions share their agenda date; all requests are intercepted."""
import argparse
import inspect
import json
import mimetypes
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assistant_context import ContextPaths
from phone_api import build_phone_snapshot
from playwright.sync_api import sync_playwright, expect


def check(dist):
    origin = json.loads((dist / 'build-info.json').read_text())['public_origin']
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
        paths = ContextPaths(**{key: Path(temporary) / key for key in inspect.signature(ContextPaths).parameters})
        booker = build_phone_snapshot(paths=paths)
        booker['agenda'].update(available=True, stale=False, closed_dates=['2026-09-09'], events=[{
            'date': '2026-09-10', 'start_time': '17:00', 'end_time': '18:00',
            'room': 'Later booked room', 'title': 'Reservation', 'is_reservation': True,
        }, {
            'date': '2026-09-10', 'start_time': '12:00', 'end_time': '13:00',
            'room': 'Test practice room', 'title': 'Reservation', 'is_reservation': True,
        }])
        candidate = {'start_time': '16:00', 'end_time': '17:00', 'room': 'Example room',
                     'potential_minutes': 60, 'reason': 'Example planned session'}
        booker['plan'].update(available=True, stale=True, days=[
            {'date': day, 'existing_minutes': 60 if day == '2026-09-10' else 0,
             'target_minutes': 180, 'reason': 'Example plan', 'primary': candidate,
             'additional': [dict(candidate, start_time='11:00', end_time='12:00')]}
            for day in ('2026-09-10', '2026-09-11')])
        state = {'busy': False, 'messages': [], 'event_cursor': 0, 'stream_generation': 'closures-test',
                 'booker': booker, 'unresolved_reserved_count': 0}
        for engine in ('chromium', 'webkit'):
            booker['plan'].update(stale=True,refresh=None)
            for day in booker['plan']['days']: day['target_minutes']=180
            browser = getattr(playwright, engine).launch(headless=True)
            page = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True,
                                    has_touch=True, service_workers='block')
            page.add_init_script('window.EventSource = class { addEventListener() {} close() {} };')
            errors = []
            live_requests = []
            fail_refresh = False
            page.on('pageerror', lambda error: errors.append(str(error)))

            def intercept(route):
                path = urlsplit(route.request.url).path
                if path.startswith('/api/'):
                    if path.endswith('/live-refresh'):
                        live_requests.append(route.request.post_data_json)
                        if fail_refresh:
                            route.fulfill(status=503,content_type='application/json',body='{}')
                            return
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
            expect(page.locator('.day-section:not(.rooms-closed) .agenda-card')).to_have_count(6)
            sections = page.locator('.day-section')
            expect(sections).to_have_count(3)
            booked_day = sections.nth(1)
            expect(booked_day.locator('.agenda-card')).to_have_count(4)
            expect(booked_day.locator('.potential-card')).to_have_count(2)
            expect(sections.nth(2).locator('.potential-card')).to_have_count(2)
            expect(page.locator('.plan-section')).to_have_count(0)
            expect(page.get_by_text('Previous plan · refresh needed')).to_be_visible()
            for width in (320, 390, 844):
                page.set_viewport_size({'width': width, 'height': 844})
                booked_day.scroll_into_view_if_needed()
                cards = booked_day.locator(':scope > .agenda-card')
                expect(cards.locator('.event-time strong')).to_have_text(['11:00', '12:00', '16:00', '17:00'])
                expect(cards.nth(0)).to_have_class('agenda-card potential-card')
                expect(cards.nth(2)).to_have_class('agenda-card potential-card')
                expect(booked_day.locator('.potential-card button')).to_have_count(0)
                assert booked_day.locator('.plan-date').bounding_box()['y'] < cards.first.bounding_box()['y']
                assert len({round(card.locator('.event-time').bounding_box()['x']) for card in cards.all()}) == 1
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                if width == 390:
                    page.screenshot(path=str(dist.parent / f'planned-date-{engine}.png'))
            booker['agenda']['available'] = False
            page.reload()
            page.get_by_role('button', name='My Week', exact=True).last.click()
            expect(page.locator('.potential-card')).to_have_count(4)
            booker['agenda']['available'] = True

            # A save immediately invalidates the old plan. Keep its sessions
            # visible and follow local snapshots without launching another scan.
            booker['preferences']['practice_plan'].update(enabled=True,default_hours=4)
            booker['plan']['refresh']={'state':'running','message':'Checking the saved preferences.'}
            live_requests.clear()
            page.reload()
            page.get_by_role('button',name='My Week',exact=True).last.click()
            expect(page.get_by_text('Previous plan · updating',exact=True)).to_be_visible()
            expect(page.get_by_text('Updating your plan for 4 hours per day…',exact=True)).to_be_visible()
            expect(page.locator('.potential-card')).to_have_count(4)
            expect(page.get_by_role('button',name='Updating plan…',exact=True)).to_be_disabled()
            assert not live_requests, live_requests
            for day in booker['plan']['days']: day['target_minutes']=240
            booker['plan'].update(stale=False,refresh={'state':'completed','message':'Plan refreshed.'})
            expect(page.locator('.plan-date').first).to_contain_text('4h target',timeout=10000)
            expect(page.get_by_text('Previous plan · updating',exact=True)).to_have_count(0)
            refresh=page.get_by_role('button',name='Refresh plan',exact=True)
            expect(refresh).to_be_enabled()
            fail_refresh=True
            refresh.click()
            expect(page.get_by_text('Live Asimut refresh did not finish. The last checked schedule remains visible.',exact=True)).to_be_visible()
            expect(page.locator('.potential-card')).to_have_count(4)
            expect(refresh).to_be_enabled()
            assert live_requests[-1]=={'scope':'plan','force':True}, live_requests
            fail_refresh=False
            refresh.click()
            expect(page.get_by_text('Live Asimut refresh did not finish. The last checked schedule remains visible.',exact=True)).to_have_count(0)
            expect(refresh).to_be_enabled()
            assert not errors, errors
            browser.close()
            print(f'PASS {engine}: matching dates, multiple sessions, stale retention, four-hour replanning, explicit refresh/failure/retry, and narrow layouts')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True)
    check(parser.parse_args().dist.resolve())
