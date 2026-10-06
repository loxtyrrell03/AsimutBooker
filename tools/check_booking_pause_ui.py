"""Shared Pause/Unpause browser regression with invented data and isolated APIs."""
import argparse
import importlib.util
import inspect
import json
import mimetypes
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
TEMPO = ROOT.parent / 'tempo'
sys.path[:0] = [str(ROOT), str(TEMPO)]
from assistant_context import ContextPaths
from phone_api import build_phone_snapshot
from booking_pause import read_pause, save_pause, PauseConflict
from playwright.sync_api import sync_playwright, expect


def check(dist, output):
    output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('tempo_pause_example', TEMPO / 'tools/qa_practice.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
        folder = Path(temporary)
        provider = fixture.FakeGoogle()
        provider.connected = False
        app = fixture.Tempo(folder / 'tempo', provider=provider)
        day = fixture.datetime.now(fixture.ZoneInfo('Europe/London')).date().isoformat()
        app.practice = fixture.ExamplePractice(app, day, 'success')
        app.booker = SimpleNamespace(read=app.practice.source_read, payload=app.practice.status_data())
        app.practice_document = lambda force=False: app.practice.read(force)
        tempo_state = app.state()
        paths = ContextPaths(**{key: folder / key for key in inspect.signature(ContextPaths).parameters})
        booker = build_phone_snapshot(paths=paths)
        state = dict(busy=True, messages=[], event_cursor=0, stream_generation='example-pause',
                     booker=booker, unresolved_reserved_count=0)
        origins = {'phone': json.loads((dist / 'build-info.json').read_text())['public_origin'],
                   'tempo': 'https://lox-pc.tail89d19b.ts.net:14443'}
        checks = []
        for engine in ('chromium', 'webkit'):
            for width in (320, 390):
                settings = folder / f'{engine}-{width}.json'
                settings.write_text('{"owner": "example-kept"}')
                browser = getattr(playwright, engine).launch(headless=True)
                context = browser.new_context(viewport={'width': width, 'height': 844}, is_mobile=True, has_touch=True, service_workers='block')
                context.add_init_script('window.EventSource = class {addEventListener() {} close() {}};')
                pages = {name: context.new_page() for name in origins}
                errors, writes = [], []
                fault = {'lose': False}
                def route_request(route):
                    request = route.request
                    parsed = urlsplit(request.url)
                    is_tempo = parsed.port == 14443
                    path = parsed.path
                    if path in ('/api/v1/booking-pause', '/api/practice/pause'):
                        if request.method == 'POST':
                            writes.append(request.post_data_json)
                            try:
                                result = save_pause(request.post_data_json, settings)
                            except PauseConflict:
                                route.fulfill(status=409, content_type='application/json', body='{}')
                                return
                            if fault['lose']:
                                route.abort()
                                return
                        else:
                            result = read_pause(settings)
                    elif path.startswith('/api/'):
                        if is_tempo:
                            result = ({'csrf': 'example-csrf', 'state': tempo_state} if path == '/api/bootstrap'
                                      else app.practice.read() if path == '/api/practice' else tempo_state)
                        else:
                            result = {'csrf_token': 'example-csrf', 'bootstrap': state} if path.endswith('/session') else state
                    else:
                        asset = (TEMPO / 'static' if is_tempo else dist) / (path.lstrip('/') or 'index.html')
                        if not asset.is_file():
                            route.fulfill(status=404)
                            return
                        route.fulfill(path=asset, content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
                        return
                    route.fulfill(content_type='application/json', body=json.dumps(result))
                context.route('**/*', route_request)
                for name, page in pages.items():
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(origins[name] + ('/#practice' if name == 'tempo' else '/'))
                    expect(page.get_by_role('button', name='Pause bookings', exact=True)).to_be_enabled()
                phone, tempo = pages['phone'], pages['tempo']
                phone.get_by_role('button', name='Pause bookings', exact=True).click()
                expect(phone.get_by_role('button', name='Unpause bookings', exact=True)).to_be_enabled()
                expect(tempo.get_by_role('button', name='Unpause bookings', exact=True)).to_be_enabled(timeout=10000)
                expect(tempo.get_by_role('button', name='Check bookings now', exact=True)).to_be_disabled()
                for name, page in pages.items():
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (name, width, 'overflow')
                    button = page.get_by_role('button', name='Unpause bookings', exact=True)
                    assert button.bounding_box()['height'] >= 44
                    page.screenshot(path=str(output / f'{name}-{engine}-{width}-paused.png'), full_page=True)
                phone.reload()
                expect(phone.get_by_role('button', name='Unpause bookings', exact=True)).to_be_enabled()
                tempo.get_by_role('button', name='Unpause bookings', exact=True).click()
                expect(phone.get_by_role('button', name='Pause bookings', exact=True)).to_be_enabled(timeout=10000)
                expect(tempo.get_by_role('button', name='Check bookings now', exact=True)).to_be_enabled()
                # The server saves once but its reply is lost. Review only reads.
                count = len(writes)
                fault['lose'] = True
                phone.get_by_role('button', name='Pause bookings', exact=True).click()
                expect(phone.get_by_role('button', name='Refresh booking status', exact=True)).to_be_visible()
                phone.wait_for_timeout(3500)
                assert len(writes) == count + 1
                fault['lose'] = False
                phone.get_by_role('button', name='Refresh booking status', exact=True).click()
                expect(phone.get_by_role('button', name='Unpause bookings', exact=True)).to_be_enabled()
                assert len(writes) == count + 1
                assert json.loads(settings.read_text())['owner'] == 'example-kept'
                assert not errors, errors
                checks.append(dict(engine=engine, width=width, shared_state=True, reload=True, lost_reply_no_replay=True))
                browser.close()
        app.store.db.close()
    (output / 'browser.json').write_text(json.dumps(checks, indent=2))
    print('PASS shared phone/Tempo Pause/Unpause, active assistant, reload, lost reply, 320/390px in Chromium/WebKit')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    check(args.dist.resolve(), args.output.resolve())
