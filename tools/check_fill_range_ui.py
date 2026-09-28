"""Exercise phone Fill without network or live booking writes."""
import argparse
import inspect
import json
import mimetypes
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from assistant_context import ContextPaths
from phone_api import build_phone_snapshot
from playwright.sync_api import sync_playwright,expect


def check(dist):
    origin=json.loads((dist/'build-info.json').read_text())['public_origin']
    day='2030-10-14'
    with tempfile.TemporaryDirectory() as folder,sync_playwright() as p:
        paths=ContextPaths(**{key:Path(folder)/f'missing-{key}' for key in inspect.signature(ContextPaths).parameters})
        for browser_name in ('chromium','webkit'):
            browser=getattr(p,browser_name).launch(headless=True)
            page=browser.new_page(viewport={'width':390,'height':844},has_touch=True,service_workers='block')
            page.add_init_script('window.EventSource=class {addEventListener(){}close(){}};')
            calls,errors=[],[];job=None;lost=False
            page.on('pageerror',lambda e:errors.append(str(e)))
            def bootstrap():
                booker=build_phone_snapshot(paths=paths)
                booker['agenda'].update(available=True,stale=False,events=[{'date':day,'start_time':'12:00','end_time':'12:30','room':'Example','event_id':99,'is_reservation':True,'title':'Reservation'}])
                return {'booker':booker,'busy':False,'messages':[],'system_job':job,'event_cursor':0,
                        'stream_generation':'fill-fixture','unresolved_reserved_count':0,'active_client_message_id':None}
            def result():
                return {'covered_minutes':90,'requested_minutes':120,'range':{'date':day,'start_time':'11:00','end_time':'13:00'},
                    'bookings':[{'date':day,'room':'Example B','event_id':100,'start':'11:00','end':'12:00','duration_minutes':60}],
                    'remaining':[{'start':'12:30','end':'13:00','minutes':30}], 'reasons':['No eligible room is available.']}
            def respond(route,body,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps(body))
            def intercept(route):
                nonlocal job,lost
                path=urlsplit(route.request.url).path
                if path=='/api/v1/session':respond(route,{'csrf_token':'fixture','bootstrap':bootstrap()});return
                if path in ('/api/v1/bootstrap','/api/v1/refresh','/api/v1/live-refresh'):respond(route,bootstrap());return
                if path=='/api/v1/system/job':respond(route,{'job':job});return
                if path=='/api/v1/system/jobs':
                    payload=route.request.post_data_json;calls.append(payload)
                    assert payload['action']=='fill_range'
                    assert payload['args']=={'date':day,'start_time':'11:00','end_time':'13:00'}
                    if lost:route.abort();return
                    job={'request_id':payload['request_id'],'action':'fill_range','range':payload['args'],'active':True,'state':'running','text':'Checking the gaps…'}
                    respond(route,{'job':job},202);return
                if path=='/api/v1/system/stop':
                    assert route.request.post_data_json=={'request_id':job['request_id']}
                    job={**job,'active':False,'state':'stopped','text':'Stopped. Confirmed bookings remain.','result':result()}
                    respond(route,{'job':job},202);return
                if path=='/api/v1/system/fill-range-review':
                    job={**job,'active':False,'state':'partial','text':'Booking status checked.','result':result()}
                    respond(route,{'job':job},202);return
                if path=='/api/v1/system/fill-range-delivery':
                    respond(route,{'job':job,'not_started':True});return
                if path.startswith('/api/'):
                    raise AssertionError(path)
                asset=dist/(path.lstrip('/') or 'index.html')
                route.fulfill(path=asset,content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
            page.route('**/*',intercept)
            page.goto(origin)
            page.get_by_role('button',name='My Week',exact=True).last.click()
            page.get_by_role('button',name=re.compile('^Fill time range:')).click()
            panel=page.get_by_role('region',name='Fill time range',exact=True)
            expect(panel.get_by_label('Date',exact=True)).to_have_value(day)
            expect(panel.get_by_label('From',exact=True)).to_have_value('11:00')
            for width,height in ((320,568),(390,844),(844,650)):
                page.set_viewport_size({'width':width,'height':height})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                button=panel.get_by_role('button',name='Fill this range',exact=True)
                button.scroll_into_view_if_needed();assert button.bounding_box()['height']>=44
                cancel=panel.get_by_role('button',name='Cancel',exact=True)
                cancel.evaluate('(el)=>el.scrollIntoView({block: "center"})')
                nav=page.locator('.bottom-nav').bounding_box()
                box=cancel.bounding_box();assert box['y']+box['height']<=nav['y']+1
                help_button=panel.get_by_role('button',name='Help: Fill time range',exact=True)
                help_button.click();tooltip=page.get_by_role('tooltip')
                expect(tooltip).to_be_visible()
                box=tooltip.bounding_box();assert box['x']>=0 and box['x']+box['width']<=width
                page.get_by_role('button',name='Close help',exact=True).click()
                panel.screenshot(path=dist.parent/f'fill-{browser_name}-{width}.png')
            panel.get_by_role('button',name='Cancel',exact=True).click()
            assert not calls
            page.get_by_role('button',name=re.compile('^Fill time range:')).click()
            start=panel.get_by_role('button',name='Fill this range',exact=True)
            start.click();expect(start).to_be_disabled();assert len(calls)==1
            page.reload()
            page.get_by_role('button',name='View operation / Stop',exact=True).click()
            expect(panel.get_by_label('Date',exact=True)).to_have_value(day)
            expect(panel.get_by_label('From',exact=True)).to_have_value('11:00')
            expect(panel.get_by_label('Until',exact=True)).to_have_value('13:00')
            page.get_by_role('button',name='Today',exact=True).last.click()
            page.get_by_role('button',name='My Week',exact=True).last.click()
            expect(panel.get_by_role('button',name='Stop',exact=True)).to_be_visible()
            panel.get_by_role('button',name='Stop',exact=True).click()
            expect(panel.get_by_text('90 of 120 min covered',exact=True)).to_be_visible()
            expect(panel.get_by_text('12:30–13:00 · 30 min',exact=True)).to_be_visible()
            panel.get_by_role('button',name='Try remaining gaps',exact=True).click();assert len(calls)==2
            job={**job,'active':False,'state':'uncertain','text':'Needs checking.'}
            expect(panel.get_by_role('button',name='Check booking status',exact=True)).to_be_visible(timeout=6000)
            expect(start).to_be_disabled()
            panel.get_by_role('button',name='Check booking status',exact=True).click()
            expect(start).to_be_enabled()
            lost=True;start.click();expect(start).to_be_disabled()
            page.reload()
            page.get_by_role('button',name='My Week',exact=True).last.click()
            page.get_by_role('button',name=re.compile('^Fill time range:')).click()
            expect(start).to_be_disabled()
            panel.get_by_role('button',name='Check booking status',exact=True).click()
            expect(start).to_be_enabled();assert len(calls)==3
            assert not errors,errors
            browser.close()
            print(f'PASS {browser_name}: exact day, opening/Cancel without writes, 320/390/844px, progress, Stop, partial, navigation, uncertain recovery and lost delivery')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--dist',required=True,type=Path)
    check(parser.parse_args().dist.resolve())
