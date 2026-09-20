"""Exercise phone preference editors with isolated files and intercepted APIs."""
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
from phone_preferences import read_phone_preferences, save_phone_preferences, PreferenceConflict
from playwright.sync_api import sync_playwright, expect


def check(dist):
    origin = json.loads((dist / 'build-info.json').read_text())['public_origin']
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
        for engine in ('chromium', 'webkit'):
            settings = Path(temporary) / f'{engine}.json'
            settings.write_text('{}')
            paths = ContextPaths(**{key: Path(temporary) / f'missing-{key}' for key in inspect.signature(ContextPaths).parameters})
            paths.settings = settings
            browser = getattr(playwright, engine).launch(headless=True)
            page = browser.new_page(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True, service_workers='block')
            errors, writes = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def bootstrap():
                return {'booker': build_phone_snapshot(paths=paths), 'busy': False, 'messages': [], 'event_cursor': 0,
                        'stream_generation': 'isolated-test', 'active_client_message_id': None, 'unresolved_reserved_count': 0}

            def intercept(route):
                path = urlsplit(route.request.url).path
                if path == '/api/v1/system/job':
                    route.fulfill(content_type='application/json',body=json.dumps({'job': None})); return
                if path == '/api/v1/assistant/events':
                    route.fulfill(content_type='text/event-stream', body=': connected\n\n')
                    return
                if path.startswith('/api/'):
                    status = 200
                    if path == '/api/v1/preferences':
                        if route.request.method == 'POST':
                            writes.append(route.request.post_data_json)
                            try:
                                body = save_phone_preferences(writes[-1], settings)
                            except PreferenceConflict as error:
                                status, body = 409, {'message': str(error)}
                            except ValueError as error:
                                status, body = 400, {'message': str(error)}
                        else:
                            body = read_phone_preferences(settings)
                    elif path == '/api/v1/session':
                        body = {'csrf_token': 'test', 'bootstrap': bootstrap()}
                    elif path in ('/api/v1/bootstrap', '/api/v1/refresh', '/api/v1/live-refresh'):
                        body = bootstrap()
                    else:
                        raise AssertionError(f'Unexpected API request: {path}')
                    route.fulfill(status=status, content_type='application/json', body=json.dumps(body))
                    return
                asset = dist / (path.lstrip('/') or 'index.html')
                route.fulfill(path=asset, content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')

            page.route('**/*', intercept)
            page.goto(origin)
            page.get_by_role('button', name='Settings', exact=True).tap()
            page.get_by_role('button', name='Daily goal', exact=True).tap()
            page.get_by_label('Use a daily practice goal').check()
            page.get_by_label('Hours per day', exact=True).fill('')
            expect(page.get_by_label('Hours per day', exact=True)).to_have_value('')
            page.get_by_label('Hours per day', exact=True).fill('3.5')
            page.get_by_role('button', name='Save changes', exact=True).tap()
            expect(page.get_by_text('Preferences saved.', exact=False)).to_be_visible()
            assert read_phone_preferences(settings)['practice_plan']['default_hours'] == 3.5
            page.get_by_role('button', name='Daily goal', exact=True).tap()
            expect(page.get_by_label('Hours per day', exact=True)).to_have_value('3.5')
            page.get_by_label('Hours per day', exact=True).fill('5')
            page.get_by_role('button', name='Cancel', exact=True).tap()
            assert len(writes) == 1

            page.get_by_role('button', name='Preferred times', exact=True).tap()
            page.get_by_label('Use preferred times', exact=True).check()
            page.get_by_label('Start time', exact=True).fill('12:30')
            page.get_by_label('End time', exact=True).fill('21:00')
            page.get_by_label('Only book within these times').check()
            page.get_by_role('button', name='Save changes', exact=True).tap()
            expect(page.get_by_text('Preferences saved.', exact=False)).to_be_visible()
            assert read_phone_preferences(settings)['time_preferences']['start_time'] == '12:30'

            page.get_by_role('button', name='Practice days', exact=True).tap()
            page.get_by_label('Practice date', exact=True).fill('2026-10-01')
            page.get_by_label('Allow automatic bookings on this date').uncheck()
            page.get_by_role('button', name='Save changes', exact=True).tap()
            expect(page.get_by_text('Preferences saved.', exact=False)).to_be_visible()
            assert read_phone_preferences(settings)['disabled_dates'] == ['2026-10-01']

            page.get_by_role('button', name='Favourite rooms', exact=True).tap()
            page.get_by_role('button', name='Move B0.29 up', exact=True).tap()
            page.get_by_label('Weston Gallery', exact=True).uncheck()
            page.get_by_role('button', name='Save changes', exact=True).tap()
            expect(page.get_by_text('Preferences saved.', exact=False)).to_be_visible()
            assert read_phone_preferences(settings)['room_preferences']['ordered_rooms'][1] == 'B0.29'
            assert read_phone_preferences(settings)['room_preferences']['excluded_rooms'] == ['Weston Gallery']

            page.get_by_role('button', name='Daily goal', exact=True).tap()
            expect(page.get_by_label('Hours per day', exact=True)).to_have_value('3.5')
            current = read_phone_preferences(settings)
            save_phone_preferences({'revision': current['revision'], 'changes': {'practice_plan': {'default_hours': 4}}}, settings)
            page.get_by_role('button', name='Save changes', exact=True).tap()
            expect(page.get_by_text('Settings changed elsewhere.', exact=False)).to_be_visible()
            page.get_by_role('button', name='Reload settings', exact=True).tap()
            expect(page.get_by_label('Hours per day', exact=True)).to_have_value('4')
            page.get_by_role('button', name='Cancel', exact=True).tap()
            page.get_by_role('button', name='Edit all practice settings', exact=True).tap()
            expect(page.get_by_label('Hours per day', exact=True)).to_have_value('4')
            page.get_by_label('Hours per day', exact=True).fill('2.5')
            page.get_by_label('Start time', exact=True).fill('13:00')
            page.get_by_role('button', name='Save changes', exact=True).tap()
            expect(page.get_by_text('Preferences saved.', exact=False)).to_be_visible()
            assert read_phone_preferences(settings)['practice_plan']['default_hours'] == 2.5
            assert read_phone_preferences(settings)['time_preferences']['start_time'] == '13:00'
            # Navigation must keep a draft; selecting another date must not
            # silently throw away the previous date's pending edit.
            page.get_by_role('button', name='Edit all practice settings', exact=True).tap()
            page.get_by_label('Hours per day', exact=True).fill('6')
            page.get_by_label('Practice date', exact=True).fill('2026-10-02')
            page.get_by_label('Allow automatic bookings on this date').uncheck()
            page.get_by_label('Practice date', exact=True).fill('2026-10-03')
            page.get_by_label('Hours for this date', exact=False).fill('3')
            page.get_by_role('button', name='Today', exact=True).tap()
            page.get_by_role('button', name='Settings', exact=True).tap()
            expect(page.get_by_label('Hours per day', exact=True)).to_have_value('6')
            page.get_by_label('Practice date', exact=True).fill('2026-10-02')
            expect(page.get_by_label('Allow automatic bookings on this date')).not_to_be_checked()
            page.get_by_role('button', name='Save changes', exact=True).tap()
            expect(page.get_by_text('Preferences saved.', exact=False)).to_be_visible()
            saved = read_phone_preferences(settings)
            assert saved['practice_plan']['default_hours'] == 6
            assert saved['practice_plan']['date_overrides']['2026-10-03'] == 3
            assert '2026-10-02' in saved['disabled_dates']
            page.get_by_role('button', name='Booking strategy', exact=True).tap()
            expect(page.get_by_label('Improve booked rooms', exact=True)).to_be_checked()
            expect(page.get_by_label('Stop upgrades before start (hours)', exact=True)).to_have_value('0')
            page.get_by_label('Preferred block length', exact=True).select_option('60')
            page.get_by_label('Preferred rest between sessions', exact=True).select_option('30')
            page.get_by_label('Prefer fewer room changes', exact=True).check()
            page.get_by_label('Improve booked rooms', exact=True).uncheck()
            page.get_by_label('Stop upgrades before start (hours)', exact=True).fill('48')
            page.get_by_role('button', name='Save changes', exact=True).tap()
            expect(page.get_by_text('Preferences saved.', exact=False)).to_be_visible()
            daily = read_phone_preferences(settings)['booking_strategy']['daily_planning']
            assert daily['upgrade_rooms'] is False and daily['upgrade_freeze_hours'] == 48
            assert daily['preferred_block_minutes'] == 60 and daily['preferred_rest_minutes'] == 30
            assert daily['prefer_fewer_room_changes'] is True
            page.get_by_role('button', name='Booking strategy', exact=True).tap()
            expect(page.get_by_label('Improve booked rooms', exact=True)).not_to_be_checked()
            expect(page.get_by_label('Stop upgrades before start (hours)', exact=True)).to_have_value('48')
            expect(page.get_by_label('Preferred block length', exact=True)).to_have_value('60')
            expect(page.get_by_label('Preferred rest between sessions', exact=True)).to_have_value('30')
            page.get_by_role('button', name='Cancel', exact=True).tap()
            page.get_by_role('button', name='Advance quota', exact=True).tap()
            page.get_by_label('Distribution', exact=True).select_option('weighted')
            page.get_by_text('Rooms and time periods', exact=True).tap()
            page.get_by_label('Rooms to use', exact=True).select_option('selected')
            page.get_by_label('Add advance room', exact=True).select_option('B0.29')
            page.get_by_role('button', name='Add time period', exact=True).tap()
            page.get_by_label('start for period 1', exact=True).fill('16:00')
            page.get_by_label('end for period 1', exact=True).fill('18:00')
            page.get_by_text('Day priorities and limits', exact=True).tap()
            page.get_by_label('Monday priority', exact=True).fill('2')
            page.get_by_label('Tuesday priority', exact=True).fill('0')
            page.get_by_label('Monday advance limit (minutes)', exact=True).fill('120')
            page.get_by_text('Waiting and credit reserve', exact=True).tap()
            page.get_by_label('Keep advance credit unused (minutes)', exact=True).fill('60')
            page.get_by_label('Plan for rooms whose booking window opens later', exact=True).uncheck()
            page.get_by_label('Release credit for last-minute practice (minutes before start)', exact=True).fill('90')
            page.get_by_role('button', name='Save changes', exact=True).tap()
            expect(page.get_by_text('Preferences saved.', exact=False)).to_be_visible()
            advance = read_phone_preferences(settings)['advance_quota']
            assert advance['distribution'] == 'weighted' and advance['day_weights'][:2] == [2, 0]
            assert advance['room_order'] == ['B0.29'] and advance['periods'][0]['start'] == '16:00'
            assert advance['reserve_minutes'] == 60 and advance['fallback_lead_minutes'] == 90
            assert not advance['wait_for_opening'] and advance['day_caps_minutes'][0] == 120
            page.get_by_role('button', name='Advance quota', exact=True).tap()
            expect(page.get_by_label('Distribution', exact=True)).to_have_value('weighted')
            for title in ('Rooms and time periods', 'Day priorities and limits', 'Waiting and credit reserve'):
                page.get_by_text(title, exact=True).tap()
            # Backend errors retain the draft and permit correction.
            page.get_by_role('button', name='Add time period', exact=True).tap()
            page.get_by_role('button', name='Save changes', exact=True).tap()
            expect(page.get_by_text('Periods on the same weekday must not overlap', exact=True)).to_be_visible()
            page.get_by_role('button', name='Remove period 2', exact=True).tap()
            for width in (320, 390):
                page.set_viewport_size({'width': width, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            shot = dist.parent / f'advance-settings-{engine}.png'
            page.screenshot(path=shot, full_page=True)
            count = len(writes)
            page.get_by_role('button', name='Cancel', exact=True).tap()
            assert len(writes) == count
            assert not errors, errors
            browser.close()
            print(f'PASS {engine}: all four editors, persistence, Cancel, stale-save rejection, reload; no live actions')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True)
    check(parser.parse_args().dist.resolve())
