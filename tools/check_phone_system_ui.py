"""Rendered phone tools with intercepted HTTP and temporary state; no live PC actions."""
import argparse
import inspect
import json
import mimetypes
from datetime import date, datetime, timezone
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_settings import atomic_write_json
from agenda_snapshot import publish_agenda_snapshot
from assistant_context import ContextPaths
from phone_api import build_phone_snapshot
from phone_preferences import read_phone_preferences
from phone_system import read_view, run_local_action, SystemConflict
from playwright.sync_api import sync_playwright, expect


def check(dist):
    origin = json.loads((dist / 'build-info.json').read_text(encoding='utf-8'))['public_origin']
    output = dist.parent / 'system-ui'; output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as p:
        for engine in ['chromium', 'webkit']:
            root = Path(temporary) / engine
            atomic_write_json(root / 'data/settings.json', {})
            atomic_write_json(root / 'data/booking_history.json', {'runs': [{'timestamp': '2030-10-15T12:00:00', 'bookings_made': 2, 'outcome': 'completed'}]})
            publish_agenda_snapshot([dict(date='2030-10-15', startTime='10:00', endTime='11:00', title='Class A', isReservation=False, room=None, eventId=12)], [date(2030,10,15)], observed_at=datetime.now(timezone.utc), path=root / 'data/agenda_snapshot.json')
            paths = ContextPaths(**{key: root / f'missing-{key}' for key in inspect.signature(ContextPaths).parameters})
            paths.settings = root / 'data/settings.json'
            browser = getattr(p, engine).launch(headless=True)
            page = browser.new_page(viewport={'width':320,'height':844},is_mobile=True,has_touch=True,service_workers='block')
            state = {'job': None, 'mode': 'ok', 'stop': 'ok'}
            writes, errors = [], []
            page.on('pageerror',lambda error: errors.append(str(error)))

            def bootstrap():
                return {'booker':build_phone_snapshot(paths=paths),'system_job':state['job'],'busy':False,'messages':[],
                        'event_cursor':0,'stream_generation':'system-test','active_client_message_id':None,'unresolved_reserved_count':0}

            def reply(route,body,status=200):
                route.fulfill(content_type='application/json',body=json.dumps(body),status=status)

            def route(request):
                path=urlsplit(request.request.url).path
                if path=='/api/v1/session': reply(request,{'csrf_token':'test','bootstrap':bootstrap()})
                elif path=='/api/v1/assistant/events': request.fulfill(content_type='text/event-stream',body=': connected\n\n')
                elif path in ['/api/v1/bootstrap','/api/v1/refresh','/api/v1/live-refresh']: reply(request,bootstrap())
                elif path=='/api/v1/preferences': reply(request,read_phone_preferences(paths.settings))
                elif path=='/api/v1/system/job': reply(request,{'job':state['job']})
                elif path=='/api/v1/system/jobs':
                    payload=request.request.post_data_json; writes.append(payload)
                    assert request.request.headers['x-asimut-csrf']=='test'
                    action=payload['action']
                    if state['mode']=='reject': reply(request,{'message':'Synthetic stale view. Reload and review.'},409);return
                    job=dict(request_id=payload['request_id'],action=action,active=False,state='completed',text='Fixture operation completed',updated_at=datetime.now(timezone.utc).isoformat())
                    if action in ['run','run_visible']:
                        job.update(active=True,state='running',text='Checking a booking opportunity…')
                    elif action=='scan':
                        job['result']={'scan':{'observed_at':'2030-10-15T12:00:00Z','scanned_dates':['2030-10-15'],'unavailable_dates':['2030-10-17'],
                                               'rows':[{'date':'2030-10-15','room':'B0.14','start':'10:00','end':'12:00','minutes':120},
                                                       {'date':'2030-10-15','room':'B0.29','start':'12:00','end':'12:30','minutes':30}]}}
                    elif action in ['events_save','config_save','history_clear']:
                        try: result=run_local_action(action,payload['args'],root);job['text']=result['message']
                        except SystemConflict as exc: job.update(state='rejected',text=str(exc))
                    state['job']=job
                    if state['mode']=='lost': request.abort();return
                    reply(request,{'accepted':True,'job':job},202)
                elif path=='/api/v1/system/stop':
                    if state['stop']=='reject': reply(request,{'message':'Stop rejected'},403);return
                    assert request.request.post_data_json['request_id']==state['job']['request_id']
                    state['job'].update(active=False,state='stopped',text='Stopped after the current step.',updated_at=datetime.now(timezone.utc).isoformat())
                    reply(request,{'accepted':True,'job':state['job']},202)
                elif path=='/api/v1/system/schedule': reply(request,{'task':{'task_name':'AsimutBooker_Recurring','healthy':True,'state':'Ready','next_run':'2030-10-15T13:13:00Z','problem':''}})
                elif path.startswith('/api/v1/system/'): reply(request,read_view(path.split('/')[-1],root))
                elif path.startswith('/api/'): raise AssertionError(path)
                else:
                    asset=dist/(path.lstrip('/') or 'index.html')
                    request.fulfill(path=asset,content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')

            def tap(name): page.get_by_role('button',name=name,exact=True).tap()
            def back(): tap('Back to tools')
            def no_overflow():
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal page overflow'
                boxes=page.locator('button:visible,input:visible,select:visible').evaluate_all('(elements) => elements.map(e=>({text:e.textContent,left:e.getBoundingClientRect().left,right:e.getBoundingClientRect().right}))')
                assert all(b['left']>=-1 and b['right']<=page.viewport_size['width']+1 for b in boxes), boxes

            page.route('**/*',route);page.goto(origin);tap('Settings')
            expect(page.get_by_role('button',name='Automatic scheduling',exact=True)).to_be_visible()
            no_overflow();page.screenshot(path=str(output/f'{engine}-settings-320.png'),full_page=True)
            tap('Automatic scheduling');expect(page.get_by_text('Installed and ready',exact=True)).to_be_visible()
            tap('Remove schedule');expect(page.get_by_role('alertdialog')).to_be_visible();assert len(writes)==0
            no_overflow();page.screenshot(path=str(output/f'{engine}-schedule-confirm-320.png'),full_page=True)
            tap('Keep current state');assert len(writes)==0
            tap('Remove schedule');tap('Remove automatic schedule');assert writes[-1]['action']=='schedule_remove'
            back();tap('Run manually');tap('Check / repair login');assert writes[-1]['action']=='login'
            tap('Help: Login recovery')
            expect(page.get_by_role('tooltip')).to_be_visible()
            box=page.get_by_role('tooltip').bounding_box();assert box['x']>=0 and box['x']+box['width']<=320
            tap('Close help')
            tap('Run in background');tap('Run Booker now')
            expect(page.get_by_role('button',name='Stop operation',exact=True)).to_be_visible()
            count=len(writes);page.reload();tap('Settings');expect(page.get_by_role('button',name='Stop operation',exact=True)).to_be_visible();assert len(writes)==count
            state['stop']='reject';tap('Stop operation');expect(page.get_by_role('alert').filter(has_text='Stop was not confirmed')).to_be_visible()
            assert state['job']['active']
            state['stop']='ok';tap('Stop operation');expect(page.get_by_text('Stopped after the current step.',exact=True)).to_be_visible()
            tap('Find available rooms');page.get_by_label('Scan date',exact=True).fill('2030-10-15');tap('Add date')
            page.get_by_label('Scan date',exact=True).fill('2030-10-17');tap('Add date');tap('Scan selected dates')
            expect(page.get_by_text('2 matching gaps',exact=True)).to_be_visible()
            page.get_by_label('Room search',exact=True).fill('B0.14');expect(page.get_by_text('1 matching gaps',exact=True)).to_be_visible()
            with page.expect_download() as item: tap('Download filtered CSV')
            downloaded=Path(item.value.path()).read_text(encoding='utf-8');assert 'B0.14' in downloaded and 'B0.29' not in downloaded
            no_overflow();page.screenshot(path=str(output/f'{engine}-scan-320.png'),full_page=True)
            back();tap('Event conflicts');page.get_by_label('Ignore Class A on 2030-10-15 at 10:00',exact=True).check()
            tap('Save event choices');tap('Save conflict choices');assert read_view('events',root)['events'][0]['ignored']
            back();tap('Advanced rules');page.get_by_label('Same-room gap (minutes)',exact=True).fill('90')
            tap('Today');tap('Settings');expect(page.get_by_label('Same-room gap (minutes)',exact=True)).to_have_value('90')
            state['mode']='reject';tap('Save advanced rules');tap('Save booking rules')
            expect(page.get_by_role('alert').filter(has_text='Synthetic stale view')).to_be_visible()
            expect(page.get_by_role('button',name='Save advanced rules',exact=True)).to_be_disabled()
            tap('Reload page');expect(page.get_by_label('Same-room gap (minutes)',exact=True)).to_have_value('90')
            state['mode']='ok';tap('Save advanced rules');tap('Save booking rules')
            assert read_view('config',root)['rules']['same_room_gap_minutes']==90
            for width,height in [(390,844),(844,390),(320,844)]:
                page.set_viewport_size({'width':width,'height':height});no_overflow()
            page.screenshot(path=str(output/f'{engine}-rules-320.png'),full_page=True)
            back();tap('Booking history');tap('Clear booking history');tap('Keep current state');assert len(read_view('history',root)['runs'])==1
            tap('Clear booking history');tap('Delete run history');assert len(read_view('history',root)['runs'])==0
            back();tap('Find available rooms');expect(page.get_by_text('1 matching gaps',exact=True)).to_be_visible();back()
            for title in ['Cancelled times kept free','Activity and logs','Old log cleanup','About and setup']:
                tap(title);no_overflow();back()
            # Lost response after a successful delivery: reload status, never replay.
            tap('Run manually');state['mode']='lost';tap('Run in background');tap('Run Booker now')
            expect(page.get_by_role('alert')).to_be_visible();count=len(writes)
            page.get_by_role('button',name='Reload status',exact=True).last.tap()
            expect(page.get_by_role('button',name='Stop operation',exact=True)).to_be_visible();assert len(writes)==count
            state['mode']='ok';tap('Stop operation')
            assert not errors,errors
            browser.close();print(f'PASS {engine}: all tools, confirmations, typed saves, scan filters/CSV, run reconnect, rejected Stop, lost response, drafts, touch help and 320/390/844px; no live operations')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--dist',required=True,type=Path)
    check(parser.parse_args().dist.resolve())
