"""Phone calendar and full settings checks with intercepted APIs and temporary data."""
import argparse
import inspect
import json
import mimetypes
from pathlib import Path
import re
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
    output = dist.parent / 'calendar-ui'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as p:
        for engine in ('chromium', 'webkit'):
            settings = Path(temporary) / f'{engine}.json'
            settings.write_text(json.dumps({'practice_plan': {'enabled': True, 'default_hours': 3, 'date_overrides': {}}}))
            paths = ContextPaths(**{key: Path(temporary) / f'missing-{key}' for key in inspect.signature(ContextPaths).parameters})
            paths.settings = settings
            browser = getattr(p, engine).launch(headless=True)
            page = browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,service_workers='block')
            errors, writes = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def bootstrap():
                return {'booker': build_phone_snapshot(paths=paths), 'busy': False, 'messages': [], 'event_cursor': 0,
                        'stream_generation': 'calendar-test', 'active_client_message_id': None, 'unresolved_reserved_count': 0}
            def route(request):
                path = urlsplit(request.request.url).path
                if path == '/api/v1/system/job':
                    request.fulfill(content_type='application/json',body=json.dumps({'job': None})); return
                if path == '/api/v1/assistant/events':
                    request.fulfill(content_type='text/event-stream',body=': connected\n\n'); return
                if path.startswith('/api/'):
                    status=200
                    if path == '/api/v1/preferences':
                        if request.request.method=='POST':
                            writes.append(request.request.post_data_json)
                            try: body=save_phone_preferences(writes[-1],settings)
                            except PreferenceConflict as exc: status,body=409,{'message':str(exc)}
                            except ValueError as exc: status,body=400,{'message':str(exc)}
                        else: body=read_phone_preferences(settings)
                    elif path=='/api/v1/session': body={'csrf_token':'test','bootstrap':bootstrap()}
                    elif path in ('/api/v1/bootstrap','/api/v1/refresh','/api/v1/live-refresh'): body=bootstrap()
                    else: raise AssertionError(path)
                    request.fulfill(status=status,content_type='application/json',body=json.dumps(body)); return
                asset=dist/(path.lstrip('/') or 'index.html')
                request.fulfill(path=asset,content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
            page.route('**/*',route)
            page.goto(origin)
            page.get_by_role('button',name='Calendar',exact=True).tap()
            page.get_by_label('Go to date',exact=True).fill('2030-10-15')
            expect(page.get_by_label('Book on this day',exact=True)).to_be_visible()
            page.get_by_label('Book on this day',exact=True).uncheck()
            page.get_by_label('Target hours',exact=True).fill('2.5')
            page.get_by_label('Preferred time',exact=True).select_option('custom')
            page.get_by_label('Day start time',exact=True).fill('17:00')
            page.get_by_label('Day end time',exact=True).fill('19:00')
            page.get_by_label('Only book within this day’s times').check()
            page.get_by_role('button',name='Settings',exact=True).tap()
            page.get_by_role('button',name='Calendar',exact=True).tap()
            expect(page.get_by_label('Day start time',exact=True)).to_have_value('17:00')
            page.get_by_role('button',name='Save changes',exact=True).tap()
            expect(page.get_by_text('Calendar changes saved.',exact=True)).to_be_visible()
            saved=read_phone_preferences(settings)
            assert saved['disabled_dates']==['2030-10-15']
            assert saved['practice_plan']['date_overrides']['2030-10-15']==2.5
            assert saved['date_time_preferences']['2030-10-15']['start_time']=='17:00'
            assert saved['time_preferences']['enabled'] is False
            page.get_by_role('button',name='Select several days',exact=True).tap()
            page.get_by_role('button',name=re.compile('^Wednesday 16 October,')).tap()
            page.get_by_role('button',name=re.compile('^Thursday 17 October,')).tap()
            page.get_by_role('button',name='Disable selected days',exact=True).tap()
            page.get_by_label('Preferred time for selected days',exact=True).select_option('custom')
            page.get_by_label('Selected days start',exact=True).fill('10:00')
            page.get_by_label('Selected days end',exact=True).fill('12:00')
            page.get_by_role('button',name='Apply preferred time',exact=True).tap()
            page.get_by_role('button',name='Save changes',exact=True).tap()
            expect(page.get_by_text('Calendar changes saved.',exact=True)).to_be_visible()
            saved=read_phone_preferences(settings)
            assert saved['disabled_dates']==['2030-10-15','2030-10-16','2030-10-17']
            assert saved['date_time_preferences']['2030-10-16']['start_time']=='10:00'
            assert saved['date_time_preferences']['2030-10-17']['start_time']=='10:00'
            page.get_by_role('button',name='Finish selecting days',exact=True).tap()
            page.get_by_label('Preferred time',exact=True).select_option('default')
            # Another device changes the default before this draft is saved.
            save_phone_preferences({'revision':saved['revision'],'changes':{'practice_plan':{'default_hours':4}}},settings)
            page.get_by_role('button',name='Save changes',exact=True).tap()
            expect(page.get_by_role('alert').filter(has_text='Settings changed')).to_be_visible()
            count=len(writes)
            expect(page.get_by_role('button',name='Save changes',exact=True)).to_be_disabled()
            page.get_by_role('button',name='Reload saved values; keep draft',exact=True).tap()
            expect(page.get_by_role('button',name='Save changes',exact=True)).to_be_enabled()
            assert len(writes)==count
            page.get_by_role('button',name='Save changes',exact=True).tap()
            expect(page.get_by_text('Calendar changes saved.',exact=True)).to_be_visible()
            assert '2030-10-17' not in read_phone_preferences(settings)['date_time_preferences']
            for width in (320,390,844):
                page.set_viewport_size({'width':width,'height':844 if width<800 else 390})
                for mode in ('month','fortnight','week','3days','plan'):
                    page.get_by_label('View',exact=True).select_option(mode)
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'),(engine,width,mode)
                page.get_by_label('View',exact=True).select_option('month')
            page.set_viewport_size({'width':320,'height':844})
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path=str(output/f'{engine}-calendar-320.png'))
            page.get_by_role('button',name='Settings',exact=True).tap()
            page.get_by_role('button',name='Room requirements',exact=True).tap()
            page.get_by_label('Required features',exact=True).fill('grand piano, adjustable stool')
            page.get_by_label('Minimum useful block (minutes)',exact=True).select_option('45')
            page.get_by_role('button',name='Save changes',exact=True).tap()
            expect(page.get_by_text('Preferences saved.',exact=False)).to_be_visible()
            assert read_phone_preferences(settings)['room_preferences']['minimum_block_minutes']==45
            page.get_by_role('button',name='Booking strategy',exact=True).tap()
            page.get_by_label('Book furthest dates first',exact=True).check()
            page.get_by_label('Better later rooms required',exact=True).select_option('3')
            page.get_by_role('button',name='Help: Better later rooms required',exact=True).tap()
            expect(page.get_by_role('tooltip')).to_be_visible()
            box=page.get_by_role('tooltip').bounding_box()
            assert 0<=box['x'] and box['x']+box['width']<=320
            page.screenshot(path=str(output/f'{engine}-strategy-help-320.png'))
            page.get_by_role('button',name='Close help',exact=True).tap()
            expect(page.get_by_role('tooltip')).to_have_count(0)
            page.get_by_role('button',name='Save changes',exact=True).tap()
            expect(page.get_by_text('Preferences saved.',exact=False)).to_be_visible()
            assert read_phone_preferences(settings)['booking_strategy']['daily_planning']['minimum_later_options']==3
            assert errors==[], errors
            browser.close()
            print(f'PASS {engine}: calendar day/bulk edits, per-day times, draft navigation, stale writes, five modes, 320/390/844px, room requirements, strategy and touch help; no live APIs')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--dist',type=Path,required=True)
    check(parser.parse_args().dist.resolve())
