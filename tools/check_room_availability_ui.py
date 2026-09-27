"""Exercise Rooms on both browser engines with synthetic, intercepted data only."""
import argparse
from datetime import datetime, timedelta, timezone
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
from room_catalog import SITE_TIMEZONE
from room_grid import normalize_day
from tests.test_room_grid import fixture
from playwright.sync_api import sync_playwright, expect


def check(dist):
    origin=json.loads((dist/'build-info.json').read_text())['public_origin']
    today=datetime.now(SITE_TIMEZONE).date()
    document=normalize_day(today.isoformat(),fixture(),['B0.13','B1.15'])
    document['stale']=False
    # Enough rows to exercise the independently sticky room/time axes.
    document['rooms'] += [{**document['rooms'][0], 'name':f'Example {i}'} for i in range(12)]
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as p:
        paths=ContextPaths(**{key:Path(temporary)/f'missing-{key}' for key in inspect.signature(ContextPaths).parameters})
        for engine in ('chromium','webkit'):
            browser=getattr(p,engine).launch(headless=True)
            page=browser.new_page(viewport={'width':390,'height':844},has_touch=True,service_workers='block')
            state={'job':None,'grid':{'days':{today.isoformat():document},'message':''}}
            calls=[]; errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            def bootstrap():
                return {'booker':build_phone_snapshot(paths=paths),'busy':False,'messages':[],
                        'system_job':state['job'],'event_cursor':0,'stream_generation':'rooms-fixture',
                        'unresolved_reserved_count':0,'active_client_message_id':None}
            def intercept(route):
                path=urlsplit(route.request.url).path
                if path=='/api/v1/assistant/events':
                    route.fulfill(content_type='text/event-stream',body=': connected\n\n');return
                if path=='/api/v1/session': body={'csrf_token':'fixture','bootstrap':bootstrap()}
                elif path in ('/api/v1/bootstrap','/api/v1/refresh','/api/v1/live-refresh'):body=bootstrap()
                elif path=='/api/v1/system/job':body={'job':state['job']}
                elif path=='/api/v1/room-grid':body=state['grid']
                elif path=='/api/v1/system/jobs':
                    payload=route.request.post_data_json;calls.append(payload)
                    assert route.request.headers['x-asimut-csrf']=='fixture'
                    assert payload['action']=='scan' and all(today.isoformat()<=d<=(today+timedelta(days=7)).isoformat() for d in payload['args']['dates'])
                    state['job']={'request_id':payload['request_id'],'action':'scan','active':True,'state':'running','text':'Reading rooms…'}
                    body={'job':state['job'],'accepted':True}
                elif path=='/api/v1/system/stop':
                    assert route.request.post_data_json['request_id']==state['job']['request_id']
                    state['job']={**state['job'],'active':False,'state':'stopped','text':'Stopped.','updated_at':datetime.now(timezone.utc).isoformat()}
                    body={'job':state['job']}
                elif path.startswith('/api/'):
                    raise AssertionError(f'Unexpected API {path}')
                else:
                    asset=dist/(path.lstrip('/') or 'index.html')
                    route.fulfill(path=asset,content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream');return
                route.fulfill(content_type='application/json',body=json.dumps(body))
            page.route('**/*',intercept)
            page.goto(origin)
            page.get_by_role('button',name='Rooms',exact=True).click()
            expect(page.get_by_role('heading',name='Room availability')).to_be_visible()
            expect(page.locator('.room-grid-name').first).to_contain_text('B0.13')
            for width,height in ((320,568),(390,844),(1040,800)):
                page.set_viewport_size({'width':width,'height':height})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (engine,width,'document overflow')
                scroll=page.locator('.room-grid-scroll')
                scroll.evaluate('e=>{e.scrollLeft=180;e.scrollTop=90}')
                page.wait_for_timeout(80)
                assert page.locator('.room-grid-name').nth(2).evaluate('e=>Math.abs(e.getBoundingClientRect().left-e.closest(".room-grid-scroll").getBoundingClientRect().left)<3')
                assert page.locator('.room-grid-axis').evaluate('e=>Math.abs(e.getBoundingClientRect().top-e.closest(".room-grid-scroll").getBoundingClientRect().top)<3')
                scroll.evaluate('e=>{e.scrollLeft=100;e.scrollTop=0}')
                page.screenshot(path=dist.parent/f'rooms-{engine}-{width}.png',full_page=True)
                page.locator('.room-grid-booking').first.click()
                expect(page.get_by_role('dialog')).to_contain_text('Alex Morgan')
                expect(page.get_by_role('link',name='Open in ASIMUT')).to_have_attribute('href','https://rwcmd.asimut.net/arrangement?eventId=123')
                page.get_by_role('button',name='Close booking details').click()
                page.get_by_role('button',name='Help: Room availability').tap()
                expect(page.get_by_role('tooltip')).to_be_visible()
                assert page.get_by_role('tooltip').evaluate('e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth}')
                page.get_by_role('button',name='Close help').click()
            page.get_by_role('textbox',name='Filter rooms').fill('B1.15')
            expect(page.locator('.room-grid-name')).to_have_count(1)
            expect(page.locator('.room-grid-name')).to_contain_text('Closed all day')
            page.get_by_role('textbox',name='Filter rooms').fill('missing')
            expect(page.get_by_text('No matching rooms',exact=True)).to_be_visible()
            page.get_by_role('textbox',name='Filter rooms').fill('')
            page.get_by_role('button',name='Next week',exact=True).click()
            expect(page.get_by_text('No room grid for this day yet',exact=True)).to_be_visible()
            page.locator('.room-grid-today').click()
            page.get_by_role('button',name='Refresh',exact=True).click()
            expect(page.get_by_role('button',name='Stop',exact=True)).to_be_visible()
            page.get_by_role('button',name='Stop',exact=True).click()
            expect(page.get_by_text('Stopped. Use Refresh to try again.',exact=True)).to_be_visible()
            assert len(calls)==1 and not errors, (calls,errors)
            browser.close()
    print('Rooms: Chromium/WebKit 320/390/1040, fixed axes, details, filters, help, navigation, refresh and Stop passed.')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--dist',type=Path,required=True)
    check(parser.parse_args().dist.resolve())
