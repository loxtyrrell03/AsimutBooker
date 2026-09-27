"""Exercise My Week -> one-date Save/Cancel using isolated files and APIs."""
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
from phone_preferences import read_phone_preferences,save_phone_preferences,PreferenceConflict
from playwright.sync_api import sync_playwright,expect


def check(dist):
    origin=json.loads((dist/'build-info.json').read_text())['public_origin']
    first,other='2030-10-14','2030-10-15'
    with tempfile.TemporaryDirectory() as temporary,sync_playwright() as p:
        for engine in ('chromium','webkit'):
            settings=Path(temporary)/f'{engine}.json'
            settings.write_text(json.dumps({'practice_plan':{'enabled':True,'default_hours':4},'unrelated':{'keep':True}}))
            paths=ContextPaths(**{key:Path(temporary)/f'missing-{key}' for key in inspect.signature(ContextPaths).parameters})
            paths.settings=settings
            browser=getattr(p,engine).launch(headless=True)
            page=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,service_workers='block')
            page.add_init_script('window.EventSource = class { addEventListener() {} close() {} };')
            errors,writes=[],[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def bootstrap():
                booker=build_phone_snapshot(paths=paths)
                booker['agenda'].update(available=True,stale=False)
                booker['plan'].update(available=True,stale=False,days=[{'date':key,'target_minutes':240,'existing_minutes':0,'primary':None,'additional':[],'backups':[],'reason':'Fixture day'} for key in (first,other)])
                return {'booker':booker,'busy':False,'messages':[],'event_cursor':0,'stream_generation':'week-day-test','unresolved_reserved_count':0}
            def intercept(route):
                path=urlsplit(route.request.url).path
                if path.startswith('/api/'):
                    status=200
                    if path=='/api/v1/preferences':
                        if route.request.method=='POST':
                            writes.append(route.request.post_data_json)
                            try:body=save_phone_preferences(writes[-1],settings)
                            except PreferenceConflict as exc:status,body=409,{'message':str(exc)}
                            except ValueError as exc:status,body=400,{'message':str(exc)}
                        else:body=read_phone_preferences(settings)
                    elif path=='/api/v1/system/job':body={'job':None}
                    elif path=='/api/v1/session':body={'csrf_token':'test','bootstrap':bootstrap()}
                    elif path in ('/api/v1/bootstrap','/api/v1/refresh','/api/v1/live-refresh'):body=bootstrap()
                    else:raise AssertionError(path)
                    route.fulfill(status=status,content_type='application/json',body=json.dumps(body));return
                asset=dist/(path.lstrip('/') or 'index.html')
                route.fulfill(path=asset,content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
            page.route('**/*',intercept)
            page.goto(origin)
            # Keep an unrelated calendar draft while editing one day from Week.
            page.get_by_role('button',name='Calendar',exact=True).click()
            calendar=page.get_by_role('region',name='Calendar',exact=True)
            calendar.get_by_label('Go to date',exact=True).fill(other)
            calendar.get_by_label('Target hours',exact=True).fill('2.5')
            page.get_by_role('button',name='My Week',exact=True).last.click()
            for width in (320,390,844):
                page.set_viewport_size({'width':width,'height':844})
                buttons=page.get_by_role('button',name=re.compile('^Edit day:'))
                expect(buttons).to_have_count(2)
                assert buttons.first.bounding_box()['height']>=44
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            buttons.first.click()
            editor=page.get_by_role('region',name='Day settings',exact=True)
            expect(editor.get_by_role('heading',name='Monday 14 October',exact=True)).to_be_visible()
            expect(editor.get_by_label('Go to date')).to_have_count(0)
            editor.get_by_label('Target hours',exact=True).fill('3')
            editor.get_by_label('Preferred time',exact=True).select_option('custom')
            editor.get_by_label('Day start time',exact=True).select_option('16:00')
            editor.get_by_label('Day end time',exact=True).select_option('rooms_closed')
            editor.get_by_label('Only book within this day’s times').check()
            editor.get_by_label('Book on this day',exact=True).uncheck()
            page.get_by_role('button',name='Today',exact=True).last.click()
            page.get_by_role('button',name='My Week',exact=True).last.click()
            expect(editor.get_by_label('Target hours',exact=True)).to_have_value('3')
            assert not writes
            save=editor.get_by_role('button',name='Save changes',exact=True)
            for width,height in ((320,568),(390,844),(390,460)):
                page.set_viewport_size({'width':width,'height':height})
                editor.get_by_label('Day end time',exact=True).scroll_into_view_if_needed()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert save.evaluate('''el=>{const r=el.getBoundingClientRect(),n=document.querySelector('.bottom-nav').getBoundingClientRect();return r.top>=0&&r.bottom<=n.top&&r.height>=44&&el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}''')
                page.screenshot(path=dist.parent/f'week-day-{engine}-{width}-{height}.png')
            save.click()
            expect(editor.get_by_text('Settings saved for Monday 14 October.',exact=False)).to_be_visible()
            saved=read_phone_preferences(settings)
            assert saved['practice_plan']['date_overrides']=={first:3}
            assert saved['practice_plan']['default_hours']==4
            assert saved['date_time_preferences']=={first:{'enabled':True,'start_time':'16:00','end_time':'rooms_closed','strict_mode':True}}
            assert saved['disabled_dates']==[first] and not saved['time_preferences']['enabled']
            assert json.loads(settings.read_text())['unrelated']=={'keep':True}
            editor.get_by_role('button',name='Back to My Week',exact=True).click()
            page.get_by_role('button',name=re.compile('^Edit day:')).first.click()
            expect(editor.get_by_label('Target hours',exact=True)).to_have_value('3')
            editor.get_by_label('Target hours',exact=True).fill('5')
            editor.get_by_role('button',name='Cancel',exact=True).click()
            assert read_phone_preferences(settings)['practice_plan']['date_overrides']=={first:3}
            page.get_by_role('button',name='Calendar',exact=True).click()
            expect(calendar.get_by_label('Target hours',exact=True)).to_have_value('2.5')
            page.get_by_role('button',name='My Week',exact=True).last.click()
            page.get_by_role('button',name=re.compile('^Edit day:')).first.click()
            editor.get_by_label('Target hours',exact=True).fill('')
            editor.get_by_label('Preferred time',exact=True).select_option('default')
            editor.get_by_label('Book on this day',exact=True).check()
            # A concurrent default change rejects the stale date-only save.
            saved=read_phone_preferences(settings)
            save_phone_preferences({'revision':saved['revision'],'changes':{'practice_plan':{'default_hours':5}}},settings)
            save.click()
            expect(editor.get_by_role('alert')).to_contain_text('changed')
            expect(save).to_be_disabled()
            editor.get_by_role('button',name='Reload saved values; keep draft',exact=True).click()
            expect(save).to_be_enabled()
            save.click()
            expect(editor.get_by_text('Settings saved for Monday 14 October.',exact=False)).to_be_visible()
            saved=read_phone_preferences(settings)
            assert saved['practice_plan']['default_hours']==5 and saved['practice_plan']['date_overrides']=={}
            assert saved['date_time_preferences']=={} and saved['disabled_dates']==[]
            assert all(set(write['changes'])<={'booking_days','practice_plan','date_time_preferences'} for write in writes)
            assert all('default_hours' not in write['changes'].get('practice_plan',{}) for write in writes)
            assert not errors,errors
            browser.close()
            print(f'PASS {engine}: My Week date identity, hours/time/on-off, scoped Save, draft retention, Cancel, defaults, stale-save recovery, visible narrow Save')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist',type=Path,required=True)
    check(parser.parse_args().dist.resolve())
