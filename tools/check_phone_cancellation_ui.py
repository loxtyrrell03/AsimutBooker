"""Isolated mobile checks: requests are intercepted; no live bookings change."""
import argparse
import json
import mimetypes
from datetime import date, timedelta
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phone_api import build_phone_snapshot
from playwright.sync_api import sync_playwright, expect
from tests.test_assistant_tools import AssistantToolSurfaceTests


def check(dist):
    origin = json.loads((dist / "build-info.json").read_text())["public_origin"]
    fixture = AssistantToolSurfaceTests()
    fixture.setUp()
    try:
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                day = date.today() + timedelta(days=1)
                fixture._publish([fixture._reservation(101, day, "16:00", "17:00", "B0.29")])
                browser = getattr(playwright, engine).launch(headless=True)
                page = browser.new_page(service_workers="block", viewport={"width": 390, "height": 844})
                page.add_init_script("window.EventSource = class { constructor() { window.testStream=this; this.listeners={}; setTimeout(() => this.onopen?.(), 0); } addEventListener(n, cb) { this.listeners[n]=cb; } close() {} };")
                requests, pending, errors = [], [], []
                state = {"cancellation": None, "cursor": 0}
                page.on("pageerror", lambda error: errors.append(str(error)))

                def bootstrap():
                    return dict(busy=False, messages=[], event_cursor=state["cursor"], stream_generation="cancel-test", cancellation=state["cancellation"],
                                active_client_message_id=None, unresolved_reserved_count=0,
                                booker=build_phone_snapshot(paths=fixture.paths))

                def reply(route, body):
                    route.fulfill(content_type="application/json", body=json.dumps(body))

                def intercept(route):
                    path = urlsplit(route.request.url).path
                    if path == "/api/v1/session":
                        reply(route, {"csrf_token": "test", "bootstrap": bootstrap()})
                    elif path in ("/api/v1/refresh", "/api/v1/live-refresh"):
                        reply(route, bootstrap())
                    elif path == "/api/v1/reservations/cancel":
                        requests.append(route.request.post_data_json)
                        state["cancellation"] = {"request_id": requests[-1]["request_id"], "reservation": requests[-1]["reservation"], "active": True, "text": "Waiting for the current schedule check to finish…"}
                        reply(route, {"accepted": True, "request_id": requests[-1]["request_id"]})
                    elif path.startswith("/api/"):
                        raise AssertionError(f"Unexpected API {path}")
                    else:
                        file = dist / (path.lstrip("/") or "index.html")
                        route.fulfill(path=str(file), content_type=mimetypes.guess_type(file)[0] or "application/octet-stream")

                page.route("**/*", intercept)
                page.goto(origin)
                page.get_by_label("Main navigation").get_by_role("button", name="My Week", exact=True).click()
                button = page.get_by_role("button", name="Cancel booking", exact=True)
                expect(button).to_be_enabled()
                button.click()
                expect(button).to_be_disabled()
                expect(page.get_by_text("Waiting for the current schedule check to finish…")).to_be_visible()
                expect(page.locator('.cancellation-progress .spin-slow')).to_be_visible()
                page.reload()
                expect(page.get_by_text("Waiting for the current schedule check to finish…")).to_be_visible()
                state["cursor"] += 1
                state["cancellation"]["text"] = "Opening your booking…"
                page.evaluate("event => window.testStream.listeners.update({data: JSON.stringify(event)})", {"kind": "cancellation.progress", "seq": state["cursor"], "stream_generation": "cancel-test", "cancellation": state["cancellation"]})
                expect(page.get_by_text("Opening your booking…")).to_be_visible()
                assert len(requests) == 1 and requests[0]["reservation"]["event_id"] == 101
                fixture._publish([])
                state["cursor"] += 1
                state["cancellation"].update(active=False, cancelled=True, text="Booking cancelled. This time will stay free.")
                page.evaluate("event => window.testStream.listeners.update({data: JSON.stringify(event)})", {"kind": "cancellation.progress", "seq": state["cursor"], "stream_generation": "cancel-test", "cancellation": state["cancellation"]})
                expect(page.get_by_text("Booking cancelled. This time will stay free.")).to_be_visible()
                expect(page.get_by_role("button", name="Cancel booking", exact=True)).to_have_count(0)
                assert not errors, errors
                browser.close()
                print(f"{engine}: exact cancellation, pending state and verified removal passed")
    finally:
        fixture.tearDown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    check(parser.parse_args().dist.resolve())
