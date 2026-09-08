"""Fault-injection checks for the phone UI; all APIs and files are isolated."""
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
from phone_preferences import read_phone_preferences, save_phone_preferences
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
            page.clock.install()
            # Control stream-vs-HTTP ordering without relying on network timing.
            page.add_init_script('''window.EventSource = class {
              constructor() { window.testStream = this; this.listeners = {};
                setTimeout(() => this.onopen?.(), 0); }
              addEventListener(name, callback) { this.listeners[name] = callback; }
              close() {}
            };''')
            state = {'busy': False, 'messages': [], 'event_cursor': 0, 'stream_generation': 'ux-test',
                     'active_client_message_id': None, 'unresolved_reserved_count': 0}
            pending, writes, errors = {}, [], []
            mode = {'get': 'ok', 'save': 'ok', 'message': 'delay', 'refresh': 'ok'}
            page.on('pageerror', lambda error: errors.append(str(error)))

            def bootstrap():
                return {**state, 'booker': build_phone_snapshot(paths=paths)}

            def reply(route, body, status=200):
                route.fulfill(status=status, content_type='application/json', body=json.dumps(body))

            def intercept(route):
                path = urlsplit(route.request.url).path
                if path == '/api/v1/system/job':
                    reply(route, {'job': None})
                elif path == '/api/v1/session':
                    reply(route, {'csrf_token': 'test', 'bootstrap': bootstrap()})
                elif path == '/api/v1/preferences':
                    if route.request.method == 'GET':
                        if mode['get'] == 'delay': pending['get'] = route
                        else: reply(route, read_phone_preferences(settings))
                    else:
                        writes.append(route.request.post_data_json)
                        saved = save_phone_preferences(writes[-1], settings)
                        if mode['save'] == 'lost': route.abort()
                        else: reply(route, saved)
                elif path == '/api/v1/assistant/messages':
                    pending['message'] = route
                    writes.append(route.request.post_data_json)
                elif path == '/api/v1/assistant/stop':
                    reply(route, {'error': 'stop_rejected'}, 403)
                elif path == '/api/v1/assistant/new-chat':
                    pending['reset'] = route
                elif path == '/api/v1/refresh' and mode['refresh'] == 'delay':
                    mode['refresh'] = 'ok'
                    pending['refresh'] = (route, bootstrap())
                elif path in ('/api/v1/refresh', '/api/v1/live-refresh'):
                    reply(route, bootstrap())
                elif path.startswith('/api/'):
                    raise AssertionError(f'Unexpected API: {path}')
                else:
                    asset = dist / (path.lstrip('/') or 'index.html')
                    route.fulfill(path=asset, content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')

            def emit(kind, **fields):
                state['event_cursor'] += 1
                page.evaluate('event => window.testStream.listeners.update({data: JSON.stringify(event)})',
                              {'kind': kind, 'seq': state['event_cursor'], 'stream_generation': 'ux-test', **fields})

            page.route('**/*', intercept)
            page.goto(origin)
            page.get_by_role('button', name='Settings', exact=True).tap()
            mode['get'] = 'delay'
            page.get_by_role('button', name='Daily goal', exact=True).tap()
            expect(page.get_by_role('button', name='Cancel', exact=True)).to_be_enabled()
            page.get_by_role('button', name='Cancel', exact=True).tap()
            reply(pending.pop('get'), read_phone_preferences(settings))
            expect(page.get_by_role('button', name='Daily goal', exact=True)).to_be_visible()
            page.get_by_role('button', name='Daily goal', exact=True).tap()
            page.clock.fast_forward(16_000)
            expect(page.get_by_text('Settings could not be loaded.', exact=False)).to_be_visible()
            reply(pending.pop('get'), read_phone_preferences(settings))
            page.get_by_role('button', name='Cancel', exact=True).tap()
            mode['get'], mode['save'] = 'ok', 'lost'
            page.get_by_role('button', name='Daily goal', exact=True).tap()
            page.get_by_label('Hours per day', exact=True).fill('4')
            page.get_by_role('button', name='Save changes', exact=True).tap()
            expect(page.get_by_text('The save result could not be checked.', exact=False)).to_be_visible()
            expect(page.get_by_role('button', name='Save changes', exact=True)).to_be_disabled()
            page.get_by_role('button', name='Reload settings', exact=True).tap()
            expect(page.get_by_label('Hours per day', exact=True)).to_have_value('4')
            assert len(writes) == 1, 'Uncertain save was replayed'
            page.get_by_role('button', name='Cancel', exact=True).tap()

            page.get_by_role('button', name='Assistant', exact=True).tap()
            composer = page.get_by_role('textbox', name='Message Asimut Assistant')
            composer.fill('Check my bookings')
            page.get_by_role('button', name='Send message', exact=True).tap()
            composer.fill('Keep my next draft')
            reply(pending.pop('message'), {'accepted': True}, 202)
            expect(composer).to_have_value('Keep my next draft')
            page.get_by_role('button', name='Stop assistant', exact=True).tap()
            expect(page.get_by_text('The stop request was not confirmed.', exact=False)).to_be_visible()
            state['busy'] = False
            emit('session.busy', status='ready')

            # A lost response retains the original delivery ID and a newer draft.
            composer.fill('Original uncertain message')
            page.get_by_role('button', name='Send message', exact=True).tap()
            original = writes[-1]
            composer.fill('Do not lose this draft')
            pending.pop('message').abort()
            page.get_by_role('button', name='Retry previous message', exact=True).tap()
            assert writes[-1] == original
            reply(pending.pop('message'), {'accepted': True}, 202)
            expect(composer).to_have_value('Do not lose this draft')
            emit('session.busy', status='ready')

            # A complete reset can arrive before its HTTP acknowledgement.
            page.get_by_role('button', name='Start a new chat', exact=True).tap()
            emit('chat.reset')
            emit('session.busy', status='ready')
            reply(pending.pop('reset'), {'resetting': True}, 202)
            expect(page.get_by_role('button', name='Send message', exact=True)).to_be_visible()

            # An older HTTP snapshot cannot roll back a newer busy stream event.
            mode['refresh'] = 'delay'
            page.get_by_role('button', name='Settings', exact=True).tap()
            page.get_by_role('button', name='Refresh status', exact=True).tap()
            state['busy'] = True
            emit('session.busy', status='working')
            route, old = pending.pop('refresh')
            reply(route, old)
            page.get_by_role('button', name='Assistant', exact=True).tap()
            expect(page.get_by_role('button', name='Stop assistant', exact=True)).to_be_visible()
            page.get_by_role('button', name='Today', exact=True).tap()
            page.get_by_role('button', name='Refresh bookings', exact=True).first.tap()
            expect(page.get_by_text('Wait for the assistant to finish, then refresh your bookings.', exact=True)).to_be_visible()
            assert not errors, errors
            page.unroute_all(behavior='wait')
            browser.close()
            print(f'PASS {engine}: interrupted settings, draft preservation, exact retry, rejected Stop, reset race, stale snapshot')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True)
    check(parser.parse_args().dist.resolve())
