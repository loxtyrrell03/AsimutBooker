"""Check assistant scrolling in mobile browsers using only synthetic, intercepted APIs."""
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
from playwright.sync_api import sync_playwright, expect


GEOMETRY = """() => {
  const rect = selector => {
    const r = document.querySelector(selector).getBoundingClientRect();
    return {top:r.top, bottom:r.bottom, height:r.height};
  };
  return {header:rect('.top-bar'), composer:rect('.composer-wrap'), nav:rect('.bottom-nav'),
    documentRange:document.documentElement.scrollHeight - innerHeight,
    documentY:scrollY, width:document.documentElement.scrollWidth};
}"""


def check(dist):
    origin = json.loads((dist / 'build-info.json').read_text())['public_origin']
    with tempfile.TemporaryDirectory() as temporary, sync_playwright() as playwright:
        paths = ContextPaths(**{key: Path(temporary) / f'missing-{key}'
                                for key in inspect.signature(ContextPaths).parameters})
        for engine in ('chromium', 'webkit'):
            browser = getattr(playwright, engine).launch(headless=True)
            page = browser.new_page(viewport={'width': 390, 'height': 844},
                                    is_mobile=True, has_touch=True, service_workers='block')
            page.add_init_script('''window.EventSource = class {
              constructor() { window.testStream = this; this.listeners = {};
                setTimeout(() => this.onopen?.(), 0); }
              addEventListener(name, callback) { this.listeners[name] = callback; }
              close() {}
            };''')
            state = {'busy': True, 'messages': [
                {'role': 'user' if i % 2 == 0 else 'assistant',
                 'text': f'Example message {i}. ' + 'Synthetic conversation for scrolling. ' * 10}
                for i in range(8)], 'event_cursor': 0, 'stream_generation': 'scroll-test',
                'active_client_message_id': None, 'unresolved_reserved_count': 0}
            errors, writes = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def intercept(route):
                path = urlsplit(route.request.url).path
                if path == '/api/v1/system/job':
                    body = {'job': None}
                elif path in ('/api/v1/session', '/api/v1/bootstrap', '/api/v1/refresh', '/api/v1/live-refresh'):
                    body = {**state, 'booker': build_phone_snapshot(paths=paths)}
                    if path == '/api/v1/session':
                        body = {'csrf_token': 'test', 'bootstrap': body}
                elif path == '/api/v1/assistant/stop':
                    writes.append(path)
                    body = {'stopping': True}
                elif path.startswith('/api/'):
                    raise AssertionError(f'Unexpected API: {path}')
                else:
                    asset = dist / (path.lstrip('/') or 'index.html')
                    route.fulfill(path=asset, content_type=mimetypes.guess_type(asset.name)[0]
                                  or 'application/octet-stream')
                    return
                route.fulfill(content_type='application/json', body=json.dumps(body))

            def emit(kind, **fields):
                state['event_cursor'] += 1
                page.evaluate('event => window.testStream.listeners.update({data:JSON.stringify(event)})',
                              {'kind': kind, 'seq': state['event_cursor'],
                               'stream_generation': 'scroll-test', **fields})

            def settled():
                page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')

            def controls_visible():
                assert page.get_by_role('button', name='Stop assistant').evaluate('''el => {
                  const r = el.getBoundingClientRect();
                  return r.height >= 44 && el.contains(document.elementFromPoint(r.x+r.width/2, r.y+r.height/2));
                }'''), 'Stop must remain a visible, unobstructed touch target'
                g = page.evaluate(GEOMETRY)
                assert g['documentRange'] <= 1 and g['documentY'] == 0, g
                assert g['composer']['bottom'] <= g['nav']['top'] + 1, g
                assert g['width'] <= page.viewport_size['width'], g
                return g

            page.route('**/*', intercept)
            page.goto(origin)
            page.get_by_role('button', name='Assistant', exact=True).tap()
            expect(page.get_by_role('button', name='Stop assistant')).to_be_visible()
            # This fails on the old build: its entire document scrolls behind controls.
            assert page.evaluate(GEOMETRY)['documentRange'] <= 1, 'Assistant leaks scrolling into the document'
            for i in range(3):
                emit('reasoning.delta', part=i, text=f'Example thinking summary {i}. ' + 'Checking the current practice plan. ' * 10)
            for i in range(6):
                emit('tool.status', title=f'Example activity {i}', text='Checking synthetic bookings. ' * 8, status='working')
            scroll = page.locator('.assistant-scroll')
            composer = page.get_by_role('textbox', name='Message Asimut Assistant')
            for width, height in ((390, 844), (320, 568), (390, 460)):
                page.set_viewport_size({'width': width, 'height': height})
                settled()
                before = controls_visible()
                scroll.evaluate('el => { el.scrollTop = 0; }')
                settled()
                # Wheel over the activity/conversation moves only that surface.
                box = scroll.bounding_box()
                page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2)
                if engine == 'chromium':
                    page.mouse.wheel(0, 180)
                else:
                    # Playwright cannot dispatch a wheel in mobile WebKit.
                    scroll.evaluate('el => el.scrollBy(0, 180)')
                page.wait_for_function("document.querySelector('.assistant-scroll').scrollTop > 50")
                after = controls_visible()
                assert before == after, (before, after)
                reading_position = scroll.evaluate('el => el.scrollTop')
                emit('assistant.delta', text='Example streamed reply. ' * 40)
                settled()
                assert abs(scroll.evaluate('el => el.scrollTop') - reading_position) < 2, 'New text stole the reading position'
                scroll.evaluate('el => { el.scrollTop = el.scrollHeight; }')
                settled()
                emit('assistant.delta', text=' More example output.' * 30)
                page.wait_for_function('''() => { const e = document.querySelector('.assistant-scroll');
                  return e.scrollHeight - e.clientHeight - e.scrollTop < 2; }''')
                # The activity has no second scrollbar or clipped tail.
                assert page.locator('.progress-body').evaluate('el => el.scrollHeight <= el.clientHeight + 1')
                assert page.locator('.progress-card').evaluate('el => el.scrollHeight <= el.clientHeight + 1')
                page.locator('.progress-card summary').click()
                expect(page.locator('.progress-card')).not_to_have_attribute('open', '')
                page.locator('.progress-card summary').click()
                expect(page.locator('.progress-card')).to_have_attribute('open', '')
                page.locator('.work-step').last.scroll_into_view_if_needed()
                expect(page.locator('.work-step').last).to_be_in_viewport()
                controls_visible()
                page.screenshot(path=dist.parent / f'assistant-{engine}-{width}-{height}.png')
                composer.fill('Retain this multiline draft.\n' * 12)
                composer.focus()
                settled()
                controls_visible()
                composer.fill('Retain this draft')

            # Model iOS's keyboard viewport resize AND nonzero panning offset.
            # This exercises the viewport adapter; it is not physical keyboard proof.
            page.set_viewport_size({'width': 390, 'height': 844})
            composer.fill('Retain this draft\n' * 12)
            page.evaluate('''() => {
              Object.defineProperty(visualViewport, 'height', {configurable:true, value:460});
              Object.defineProperty(visualViewport, 'offsetTop', {configurable:true, value:96});
              visualViewport.dispatchEvent(new Event('resize'));
              visualViewport.dispatchEvent(new Event('scroll'));
            }''')
            settled()
            g = controls_visible()
            assert abs(g['header']['top'] - 96) < 1 and abs(g['nav']['bottom'] - 556) < 1, g
            page.evaluate('''() => {
              delete visualViewport.height; delete visualViewport.offsetTop;
              visualViewport.dispatchEvent(new Event('resize'));
            }''')
            composer.fill('Retain this draft')
            emit('error', text='Example temporary failure. ' * 30)
            scroll.evaluate('el => { el.scrollTop = 0; }')
            settled()
            expect(page.get_by_role('button', name='Dismiss error')).to_be_in_viewport()
            controls_visible()
            page.get_by_role('button', name='Dismiss error').tap()
            page.get_by_role('button', name='Stop assistant').tap()
            assert writes == ['/api/v1/assistant/stop']
            state['busy'] = False
            emit('session.busy', status='ready')
            expect(page.get_by_role('button', name='Send message')).to_be_visible()
            page.get_by_role('button', name='Settings', exact=True).tap()
            page.evaluate('window.scrollTo(0, 200)')
            assert page.evaluate('scrollY') > 0, 'Leaving Assistant must restore normal page scrolling'
            page.get_by_role('button', name='Assistant', exact=True).tap()
            expect(composer).to_have_value('Retain this draft')
            assert page.evaluate(GEOMETRY)['documentY'] == 0
            assert not errors, errors
            browser.close()
            print(f'PASS {engine}: contained scroll, readable activity, stable controls and reading position, viewport resize, draft and Stop')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True)
    check(parser.parse_args().dist.resolve())
