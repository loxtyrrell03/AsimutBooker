"""Exercise the actual extension lookup against a lazy-loaded browser agenda."""

import contextlib
import io
import unittest
from unittest import mock

from playwright.sync_api import sync_playwright

import book_week as b


class ExtensionAgendaLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def inspect(self, *, lazy=True, legacy=False, copies=1, event_id=42,
                end="12:30", cancelled=False):
        booking = dict(eventId=42, event_url="https://rwcmd.asimut.net/arrangement?eventId=42",
                       date="2026-09-22", room="B0.13", startTime="12:00", endTime="12:30")
        identity = (f'<a href="/arrangement?eventId={event_id}">Details</a>' if legacy
                    else "")
        attr = "" if legacy else f'data-cy="event_{event_id}"'
        card = (f'<section class="as-event-panel" {attr} '
                f'style="text-decoration:{"line-through" if cancelled else "none"}">'
                f'{identity}<div>12:00 - {end}</div>'
                '<div data-cy="event-display-name">Reservation</div><div>in B0.13</div>'
                '<button data-cy="button_more" onclick="document.getElementById(\'menu\').hidden=false">More</button>'
                '</section>') * copies
        agenda = ("<div style='height:1800px'>Earlier dates</div>"
                  "<h2>Tuesday 22 September 2026</h2><main id='events'></main>"
                  "<div id='menu' hidden><mat-list-item onclick=\"location.href='/event?eventId=42'\">"
                  "Edit event</mat-list-item></div>")
        if lazy:
            import json
            agenda += ("<script>window.addEventListener('scroll',()=>{"
                       f"document.getElementById('events').innerHTML={json.dumps(card)};"
                       "},{once:true});</script>")
        else:
            agenda = agenda.replace("<main id='events'></main>", f"<main id='events'>{card}</main>")
        with self.browser.new_context(service_workers="block") as context:
            requests = []

            def site(route):
                requests.append((route.request.method, route.request.url))
                body = ("<input id='endDate' aria-label='End time' value='12:30'>"
                        if "/event?" in route.request.url else agenda)
                route.fulfill(status=200, content_type="text/html", body=body)

            context.route("**/*", site)
            page = context.new_page()
            real_wait = page.wait_for_timeout
            with (mock.patch.object(b, "safe_goto", side_effect=lambda p, url: p.goto(url)),
                  mock.patch.object(page, "wait_for_timeout", side_effect=lambda ms: real_wait(min(ms, 20))),
                  mock.patch.object(b, "refresh_extension_validation", return_value=(False, "inspection only")) as validate,
                  mock.patch.object(b, "record_pending_extension") as receipt,
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertFalse(b.edit_reservation_end_time(page, booking, "14:00"))
            receipt.assert_not_called()
            self.assertTrue(all(method == "GET" for method, _ in requests))
            return validate.call_count

    def test_lazy_exact_card_loads_before_opening_its_editor(self):
        self.assertEqual(self.inspect(), 1)

    def test_lazy_canonical_legacy_card_loads_before_opening_its_editor(self):
        self.assertEqual(self.inspect(legacy=True), 1)

    def test_duplicate_cards_are_rejected_initially_and_after_loading(self):
        for lazy in (False, True):
            for legacy in (False, True):
                with self.subTest(lazy=lazy, legacy=legacy):
                    self.assertEqual(self.inspect(lazy=lazy, legacy=legacy, copies=2), 0)

    def test_missing_wrong_changed_or_cancelled_cards_never_open_editor(self):
        for variant in ({"copies": 0}, {"event_id": 43}, {"end": "12:45"}, {"cancelled": True}):
            with self.subTest(variant=variant):
                self.assertEqual(self.inspect(**variant), 0)
