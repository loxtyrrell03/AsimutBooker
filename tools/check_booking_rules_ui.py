"""Exercise the compiled phone rules editor against isolated real preference storage."""
import argparse
import inspect
import json
import mimetypes
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_settings import save_settings, load_settings
from assistant_context import ContextPaths
from phone_api import build_phone_snapshot
from phone_preferences import read_phone_preferences, save_phone_preferences, PreferenceConflict
from playwright.sync_api import sync_playwright, expect

def check(dist):
    origin = json.loads((dist/'build-info.json').read_text())['public_origin']
    with tempfile.TemporaryDirectory() as temp, sync_playwright() as p:
        paths = ContextPaths(**{key:Path(temp)/key for key in inspect.signature(ContextPaths).parameters})
        settings=Path(temp)/'preferences.json'
        state={'busy':False,'messages':[],'event_cursor':0,'stream_generation':'rules-test',
               'booker':build_phone_snapshot(paths=paths),'unresolved_reserved_count':0}
        for engine in ('chromium','webkit'):
            save_settings({'keep':'unrelated','booking_rules':{'preset':'legacy'}},settings)
            browser=getattr(p,engine).launch(headless=True)
            page=browser.new_page(viewport={'width':390,'height':844},service_workers='block')
            page.add_init_script('window.EventSource = class { addEventListener() {} close() {} };')
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            def intercept(route):
                path=urlsplit(route.request.url).path
                status=200
                if path=='/api/v1/preferences':
                    try:
                        body=save_phone_preferences(route.request.post_data_json,settings) if route.request.method=='POST' else read_phone_preferences(settings)
                    except PreferenceConflict as e:
                        status,body=409,{'error':'preferences_changed','message':str(e)}
                    except (ValueError,RuntimeError) as e:
                        status,body=400,{'message':str(e)}
                elif path.startswith('/api/'):
                    body={'csrf_token':'fixture','bootstrap':state} if path.endswith('/session') else state
                else:
                    asset=dist/(path.lstrip('/') or 'index.html')
                    route.fulfill(path=asset,content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
                    return
                route.fulfill(status=status,content_type='application/json',body=json.dumps(body))
            page.route('**/*',intercept)
            page.goto(origin)
            page.get_by_role('button',name='Settings',exact=True).last.click()
            page.get_by_role('button',name='Booking rules',exact=True).click()
            preset=page.get_by_label('Booking rules preset',exact=True)
            expect(preset).to_have_value('legacy')
            expect(page.get_by_label('Weekday peak quota (minutes)',exact=True)).to_have_value('120')
            for width in (320,390,844):
                page.set_viewport_size({'width':width,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(dist.parent/f'rules-{engine}-{width}.png'),full_page=True)
            preset.select_option('new')
            page.get_by_role('button',name='Save changes',exact=True).click()
            expect(page.get_by_role('button',name='Booking rules',exact=True)).to_be_visible()
            assert read_phone_preferences(settings)['booking_rules']['peak_quota_minutes']==60
            page.get_by_role('button',name='Booking rules',exact=True).click()
            preset.select_option('custom')
            page.get_by_label('Advance quota (hours)',exact=True).fill('10')
            page.get_by_label('Weekday peak quota (minutes)',exact=True).fill('90')
            page.get_by_label('Free horizon (minutes)',exact=True).fill('240')
            page.get_by_label('Weekday peak end',exact=True).select_option('1440')
            page.get_by_role('button',name='Save changes',exact=True).click()
            expect(page.get_by_role('button',name='Booking rules',exact=True)).to_be_visible()
            assert read_phone_preferences(settings)['booking_rules']['peak_end_minutes']==1440
            page.get_by_role('button',name='Booking rules',exact=True).click()
            page.get_by_label('Weekday peak end',exact=True).select_option('0')
            page.get_by_role('button',name='Save changes',exact=True).click()
            expect(page.get_by_role('alert')).to_contain_text('Peak')
            assert read_phone_preferences(settings)['booking_rules']['peak_end_minutes']==1440
            current=load_settings(settings)
            current['booking_rules']={'preset':'new'}
            save_settings(current,settings)
            preset.select_option('legacy')
            page.get_by_role('button',name='Save changes',exact=True).click()
            expect(page.get_by_role('alert')).to_contain_text('changed elsewhere')
            expect(page.get_by_role('button',name='Save changes',exact=True)).to_be_disabled()
            page.get_by_role('button',name='Reload settings',exact=True).click()
            expect(preset).to_have_value('new')
            assert load_settings(settings)['keep']=='unrelated'
            assert not errors,errors
            browser.close()
            print(f'PASS {engine}: presets, custom values, midnight, invalid values, conflict/reload and narrow layouts')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist',type=Path,required=True)
    check(parser.parse_args().dist.resolve())
