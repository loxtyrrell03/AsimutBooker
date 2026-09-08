"""Exercise a built phone shell against isolated network failures, never live APIs."""
import argparse
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect


def check(dist: Path):
    origin = json.loads((dist / 'build-info.json').read_text())['public_origin']
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 390, 'height': 844}, service_workers='block')
        page = context.new_page()
        calls = []
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def intercept(route):
            request = route.request
            path = urlsplit(request.url).path
            if path.startswith('/api/'):
                calls.append(path)
                route.fulfill(status=403, content_type='application/json', body='{"error":"identity_rejected"}')
                return
            asset = dist / (path.lstrip('/') or 'index.html')
            if asset.is_file():
                route.fulfill(path=asset, content_type=mimetypes.guess_type(asset.name)[0] or 'application/octet-stream')
            else:
                route.fulfill(status=404)

        context.route('**/*', intercept)
        page.goto(origin)
        expect(page.get_by_text('Your PC responded, but private access was rejected.', exact=False)).to_be_visible()
        page.wait_for_timeout(8500)
        assert len(calls) == 4, f'Retries were not bounded: {calls}'
        assert set(calls) == {'/api/v1/session'}, 'Connection recovery attempted a booking action'
        expect(page.get_by_text('Your PC responded, but private access was rejected.', exact=False)).to_be_visible()
        page.evaluate("window.dispatchEvent(new Event('online'))")
        page.wait_for_timeout(100)
        assert len(calls) == 5, 'Connection did not resume after network recovery'
        page.goto('https://wrong-pc.example.ts.net:10443/')
        expect(page.get_by_text('Private companion', exact=True)).to_be_visible()
        before = len(calls)
        page.wait_for_timeout(1000)
        assert len(calls) == before, 'Wrong origin attempted a private session'
        assert not errors, errors
        browser.close()
    print('PASS exact-origin gate, bounded retries, access error, network recovery, no booking actions')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, required=True)
    check(parser.parse_args().dist.resolve())
