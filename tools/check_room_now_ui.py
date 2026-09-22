"""Phone room-now controls against isolated state and intercepted APIs only."""
import argparse
import inspect
import json
import mimetypes
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from assistant_context import ContextPaths
from phone_api import build_phone_snapshot
from playwright.sync_api import sync_playwright, expect


def check(dist):
    origin=json.loads((dist/'build-info.json').read_text())['public_origin']
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as p:
        paths=ContextPaths(**{key:Path(temporary)/f'missing-{key}' for key in inspect.signature(ContextPaths).parameters})
        for name in ('chromium','webkit'):
            browser=getattr(p,name).launch(headless=True,**({'args':['--disable-gpu']} if name=='chromium' else {}))
            page=browser.new_page(viewport={'width':390,'height':844},service_workers='block',has_touch=True)
            job=None;calls=[];errors=[];lost=False
            page.on('pageerror',lambda e:errors.append(str(e)))
            def bootstrap():
                return {'booker':build_phone_snapshot(paths=paths),'busy':False,'messages':[],
                        'system_job':job,'event_cursor':0,'stream_generation':'room-now-fixture',
                        'unresolved_reserved_count':0,'active_client_message_id':None}
            def respond(route,body,status=200):
                route.fulfill(status=status,content_type='application/json',body=json.dumps(body))
            def intercept(route):
                nonlocal job,lost
                path=urlsplit(route.request.url).path
                if path=='/api/v1/assistant/events':
                    route.fulfill(content_type='text/event-stream',body=': connected\n\n');return
                if path=='/api/v1/session':respond(route,{'csrf_token':'fixture','bootstrap':bootstrap()});return
                if path in ('/api/v1/bootstrap','/api/v1/refresh','/api/v1/live-refresh'):respond(route,bootstrap());return
                if path=='/api/v1/system/job':respond(route,{'job':job});return
                if path=='/api/v1/system/jobs':
                    payload=route.request.post_data_json;calls.append(payload)
                    assert payload['action']=='room_now' and payload['args']['minutes'] in (60,105,120)
                    if lost:route.abort();return
                    job={'request_id':payload['request_id'],'action':'room_now','active':True,'state':'running','text':'Checking available rooms…'}
                    respond(route,{'accepted':True,'job':job},202);return
                if path=='/api/v1/system/stop':
                    assert route.request.post_data_json=={'request_id':job['request_id']}
                    job={**job,'active':False,'state':'stopped','text':'Stopped before booking. No room was booked.'}
                    respond(route,{'accepted':True,'job':job},202);return
                if path=='/api/v1/system/room-now-review':
                    booking={'event_id':99,'room':'Example','date':'2030-09-28','start':'14:15','end':'15:00','duration_minutes':45}
                    job={**job,'active':False,'state':'completed','text':'Booked Example · 14:15–15:00 · 45 minutes.',
                         'result':{'booking':booking,'requested_minutes':120}}
                    respond(route,{'accepted':True,'job':job},202);return
                if path=='/api/v1/system/room-now-delivery':
                    respond(route,{'job':job,'not_started':True});return
                if path.startswith('/api/'):
                    raise AssertionError(f'Unexpected API request: {path}')
                asset=dist/(path.lstrip('/') or 'index.html')
                route.fulfill(path=asset,content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
            page.route('**/*',intercept)
            page.goto(origin)
            panel=page.locator('.room-now')
            start=panel.get_by_role('button',name='Find me a room now',exact=True)
            expect(start).to_be_enabled()
            for width,height in ((390,844),(320,844),(320,460)):
                page.set_viewport_size({'width':width,'height':height})
                start.scroll_into_view_if_needed()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                rect=start.bounding_box();assert rect['width']>=250 and rect['height']>=44
                panel.get_by_role('button',name='Help: Duration').click()
                tip=page.get_by_role('tooltip');expect(tip).to_be_visible()
                page.wait_for_function("() => {const r=document.querySelector('[role=tooltip]').getBoundingClientRect();return r.x>=0 && r.right<=innerWidth && r.bottom<=innerHeight;}"); bounds=tip.bounding_box()
                page.keyboard.press('Escape');expect(tip).not_to_be_visible()
            page.set_viewport_size({'width':390,'height':844})
            panel.get_by_role('button',name='Longest possible',exact=True).click()
            expect(panel.get_by_role('button',name='2 hours',exact=True)).to_have_attribute('aria-pressed','true')
            panel.get_by_role('button',name='Custom duration',exact=True).click()
            panel.get_by_label('Custom duration',exact=True).select_option('105')
            page.get_by_role('button',name='My Week',exact=True).last.click()
            page.get_by_role('button',name='Today',exact=True).click()
            expect(panel.get_by_label('Custom duration',exact=True)).to_have_value('105')
            page.reload();expect(start).to_be_enabled()
            expect(panel.get_by_label('Custom duration',exact=True)).to_have_value('105')
            panel.screenshot(path=dist.parent/f'room-now-{name}-390.png')
            start.click();expect(panel.get_by_role('button',name='Stop',exact=True)).to_be_visible()
            expect(start).to_be_disabled();assert len(calls)==1 and calls[0]['args']=={'mode':'longest','minutes':105}
            panel.get_by_role('button',name='Stop',exact=True).click();expect(start).to_be_enabled()
            panel.get_by_role('button',name='2 hours',exact=True).click();start.click()
            job={**job,'active':False,'state':'uncertain','text':'The Save result needs checking.'}
            page.reload();expect(panel.get_by_role('button',name='Check booking status')).to_be_visible()
            expect(start).to_be_disabled()
            panel.get_by_role('button',name='Check booking status').click()
            expect(panel.get_by_role('button',name='View booking')).to_be_visible()
            expect(panel).to_contain_text('requested 120 min')
            panel.get_by_role('button',name='View booking').click();expect(page.get_by_role('heading',name='Your booking')).to_be_visible()
            page.get_by_role('button',name='Back',exact=True).click()
            page.set_viewport_size({'width':320,'height':844})
            panel.screenshot(path=dist.parent/f'room-now-{name}-320.png')
            lost=True;start.click();expect(panel.get_by_role('button',name='Check booking status')).to_be_visible()
            expect(start).to_be_disabled()
            page.reload();expect(start).to_be_disabled()
            panel.get_by_role('button',name='Check booking status').click();expect(start).to_be_enabled()
            assert len(calls)==3, calls
            assert not errors,errors
            browser.close()
            print(f'PASS {name}: duration persistence, 320/390px, short viewport help, booking, Stop, uncertain recovery, lost delivery; no live writes')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--dist',type=Path,required=True)
    check(parser.parse_args().dist.resolve())
