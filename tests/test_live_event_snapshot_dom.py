"""Real-DOM regression tests for current Asimut arrangement details."""

from __future__ import annotations

import contextlib
from datetime import date, timedelta
import io
import unittest
from unittest import mock

from playwright.sync_api import sync_playwright

import book_week


class _NoWaitPage:
    def __init__(self, page):
        self._page = page

    @property
    def url(self):
        return self._page.url

    def wait_for_timeout(self, _milliseconds):
        return None

    def __getattr__(self, name):
        return getattr(self._page, name)


class CurrentArrangementDomTests(unittest.TestCase):
    EVENT_URL = "https://rwcmd.asimut.net/arrangement?eventId=3580086"

    def setUp(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True)
        self.page = self.browser.new_page()

    def tearDown(self):
        self.browser.close()
        self.playwright.stop()

    def _load(self, body: str):
        self.page.route(
            self.EVENT_URL,
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=f"<!doctype html><html><body>{body}</body></html>",
            ),
        )
        self.page.goto(self.EVENT_URL, wait_until="domcontentloaded")

    def test_current_data_cy_card_and_short_date_extract_exactly(self):
        self._load(
            """
            <div class="event-details-expanded" data-cy="event_3580086">
              <div data-cy="event-datetime">Tue 01/09/26 11:00 - 11:30</div>
              <a data-cy="event-location-link">B1.09 (Practice: Grand Piano)</a>
            </div>
            """
        )

        self.assertEqual(
            book_week.page_booking_snapshot(self.page),
            {
                "room": "B1.09",
                "date": "2026-09-01",
                "start": "11:00",
                "end": "11:30",
            },
        )

    def test_wrong_event_card_cannot_confirm_current_url(self):
        self._load(
            """
            <div class="event-details-expanded" data-cy="event_9999999">
              <div data-cy="event-datetime">Tue 01/09/26 11:00 - 11:30</div>
              <a data-cy="event-location-link">B1.09 (Practice: Grand Piano)</a>
            </div>
            """
        )

        self.assertEqual(
            book_week.page_booking_snapshot(self.page),
            {"room": None, "date": None, "start": None, "end": None},
        )

    def test_url_bound_event_card_takes_precedence_over_generic_event_root(self):
        self._load(
            """
            <app-event>
              <input aria-label="Event location" value="B1.09 (Practice: Grand Piano)">
              <input aria-label="Event date" value="2026-09-01">
              <input aria-label="Start time" value="11:00">
              <input aria-label="End time" value="11:30">
            </app-event>
            <div class="event-details-expanded" data-cy="event_3580086">
              <div data-cy="event-datetime">Wed 02/09/26 12:00 - 12:30</div>
              <a data-cy="event-location-link">B0.29 (Practice: Grand Piano)</a>
            </div>
            """
        )

        self.assertEqual(
            book_week.page_booking_snapshot(self.page),
            {
                "room": "B0.29",
                "date": "2026-09-02",
                "start": "12:00",
                "end": "12:30",
            },
        )

    def test_generic_event_root_cannot_replace_missing_url_bound_card(self):
        self._load(
            """
            <app-event>
              <input aria-label="Event location" value="B1.09 (Practice: Grand Piano)">
              <input aria-label="Event date" value="2026-09-01">
              <input aria-label="Start time" value="11:00">
              <input aria-label="End time" value="11:30">
            </app-event>
            """
        )

        self.assertEqual(
            book_week.page_booking_snapshot(self.page),
            {"room": None, "date": None, "start": None, "end": None},
        )

    def test_current_agenda_dom_extracts_exact_event_id_and_ignores_cancelled(self):
        today = date.today()
        window_dates = tuple(today + timedelta(days=offset) for offset in range(6))
        day_markup = []
        for offset, current in enumerate(window_dates):
            header = current.strftime("%A %d %B %Y").replace(" 0", " ")
            events = ""
            if offset == 2:
                events = """
                <div class="event-details-expanded" data-cy="event_3580086">
                  <div data-cy="event-datetime">11:00 - 11:30</div>
                  <span data-cy="event-display-name">Reservation</span>
                  <a data-cy="event-location-link">B1.09 (Practice: Grand Piano)</a>
                </div>
                """
            if offset == 3:
                events = """
                <div class="event-details-expanded cancelled" data-cy="event_3580087">
                  <div data-cy="event-datetime">12:00 - 12:30</div>
                  <span data-cy="event-display-name">Reservation</span>
                  <a data-cy="event-location-link">B0.29 (Practice: Grand Piano)</a>
                </div>
                """
            rendered_header = header if events else f"{header} &middot; no events"
            day_markup.append(
                f"<div><div class='day-header'>{rendered_header}</div>"
                f"<div class='day-body'>{events}</div></div>"
            )
        html = "<!doctype html><html><body>" + "".join(day_markup) + "</body></html>"
        self.page.route(
            book_week.ASIMUT_AGENDA_URL,
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=html,
            ),
        )
        page = _NoWaitPage(self.page)
        tracker = book_week.BookingTracker()

        with (
            mock.patch.object(
                book_week,
                "safe_goto",
                side_effect=lambda target_page, url: target_page.goto(
                    url, wait_until="domcontentloaded"
                ),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            event_count, reservations = book_week.scan_agenda(
                page,
                tracker,
                today,
                ignored_events=[],
                window_dates=window_dates,
            )

        self.assertEqual(event_count, 1)
        self.assertEqual(
            reservations,
            [{
                "date": (today + timedelta(days=2)).isoformat(),
                "startTime": "11:00",
                "endTime": "11:30",
                "room": "B1.09",
                "daysDiff": 2,
                "eventId": 3580086,
            }],
        )

    def test_agenda_rejects_event_id_assigned_to_previous_split_header_date(self):
        today = date.today()
        window_dates = tuple(today + timedelta(days=offset) for offset in range(6))
        day_markup = []
        for offset, current in enumerate(window_dates):
            header = current.strftime("%A %d %B %Y").replace(" 0", " ")
            if offset == 1:
                first, month, year = header.rsplit(" ", 2)
                rendered_header = (
                    f"<span>{first}</span><span> {month} {year}</span>"
                )
                events = """
                    <div class="event-details-expanded" data-cy="event_3580088">
                      <div data-cy="event-datetime">11:00 - 11:30</div>
                      <span data-cy="event-display-name">Reservation</span>
                      <a data-cy="event-location-link">B1.09 (Practice: Grand Piano)</a>
                    </div>
                """
            else:
                rendered_header = header
                events = ""
            day_markup.append(
                f"<div><div class='day-header'>{rendered_header}</div>"
                f"<div class='day-body'>{events}</div></div>"
            )
        html = "<!doctype html><html><body>" + "".join(day_markup) + "</body></html>"
        self.page.route(
            book_week.ASIMUT_AGENDA_URL,
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=html,
            ),
        )
        page = _NoWaitPage(self.page)

        with (
            mock.patch.object(
                book_week,
                "safe_goto",
                side_effect=lambda target_page, url: target_page.goto(
                    url, wait_until="domcontentloaded"
                ),
            ),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(
                RuntimeError,
                "event-card ID/date associations",
            ),
        ):
            book_week.scan_agenda(
                page,
                book_week.BookingTracker(),
                today,
                ignored_events=[],
                window_dates=window_dates,
            )


if __name__ == "__main__":
    unittest.main()
