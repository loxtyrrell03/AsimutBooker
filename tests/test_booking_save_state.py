import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import book_week
from mutation_receipts import (
    attach_event_url,
    list_pending,
    load_journal,
    mark_resolved,
    mark_verified,
    record_pending_create,
)


class _SteppingClock:
    """Deterministic monotonic clock that keeps timeout tests instantaneous."""

    def __init__(self, step=0.25):
        self.value = 0.0
        self.step = step

    def __call__(self):
        current = self.value
        self.value += self.step
        return current


class _FakeLocator:
    def __init__(self, *, count=0, visible=False, text=""):
        self._count = count
        self._visible = visible
        self._text = text

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def is_visible(self):
        return self._visible

    def text_content(self):
        return self._text


class _FakePage:
    """Small Playwright-shaped fake for post-Save classification tests."""

    AUTH_SELECTORS = {
        "[data-cy='menu-item-locations']",
        "a[href='/agenda']",
        "app-wrapper",
    }

    def __init__(
        self,
        *,
        url="https://rwcmd.asimut.net/event?eventId=0",
        url_after_waits=None,
        rejection_after_waits=None,
        rejection_text="Booking clashes with another event",
        snapshots=None,
        reload_url=None,
    ):
        self.url = url
        self.url_after_waits = dict(url_after_waits or {})
        self.rejection_after_waits = rejection_after_waits
        self.rejection_text = rejection_text
        self.snapshots = list(snapshots or [])
        self.reload_url = reload_url
        self.wait_count = 0
        self.reload_count = 0

    def is_closed(self):
        return False

    def wait_for_timeout(self, _milliseconds):
        self.wait_count += 1
        if self.wait_count in self.url_after_waits:
            self.url = self.url_after_waits[self.wait_count]

    def wait_for_load_state(self, _state, timeout=None):
        return None

    def reload(self, **_kwargs):
        self.reload_count += 1
        if self.reload_url is not None:
            self.url = self.reload_url

    def locator(self, selector):
        if selector in self.AUTH_SELECTORS:
            return _FakeLocator(count=1, visible=True, text="Asimut")
        rejected = (
            self.rejection_after_waits is not None
            and self.wait_count >= self.rejection_after_waits
        )
        if rejected and selector == ".mat-error":
            return _FakeLocator(count=1, visible=True, text=self.rejection_text)
        return _FakeLocator()

    def evaluate(self, _script):
        if not self.snapshots:
            return ""
        if len(self.snapshots) == 1:
            return self.snapshots[0]
        return self.snapshots.pop(0)


class _SnapshotScriptPage:
    def __init__(self, result):
        self.result = result
        self.script = None

    def evaluate(self, script):
        self.script = script
        return self.result


class BookingSaveStateTests(unittest.TestCase):
    BOOKING_DATE = date(2026, 8, 31)
    ROOM = "B0.29"
    START = "09:30"
    END = "11:00"
    EVENT_URL = "https://rwcmd.asimut.net/arrangement?eventId=4242"
    EXACT_SNAPSHOT = {
        "room": ROOM,
        "date": BOOKING_DATE.isoformat(),
        "start": START,
        "end": END,
    }

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.receipts_path = (
            Path(self.temporary_directory.name) / "mutation_receipts.json"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _pending_receipt(self):
        return record_pending_create(
            room=self.ROOM,
            booking_date=self.BOOKING_DATE.isoformat(),
            start=self.START,
            end=self.END,
            path=self.receipts_path,
        )

    def _journal_aliases(self):
        """Point book_week's imported receipt aliases at this test's temp file."""

        return mock.patch.multiple(
            book_week,
            attach_event_url=lambda receipt_id, event_url: attach_event_url(
                receipt_id, event_url, path=self.receipts_path
            ),
            verify_mutation_receipt=lambda receipt_id, event_url=None: mark_verified(
                receipt_id, event_url=event_url, path=self.receipts_path
            ),
            resolve_mutation_receipt=lambda receipt_id, resolution="reconciled", event_url=None: mark_resolved(
                receipt_id,
                resolution=resolution,
                event_url=event_url,
                path=self.receipts_path,
            ),
        )

    def _wait_for_outcome(self, page, receipt, *, timeout_seconds=4, clock_step=0.25):
        with self._journal_aliases(), mock.patch.object(
            book_week.time, "monotonic", side_effect=_SteppingClock(clock_step)
        ):
            return book_week.wait_for_created_booking_outcome(
                page,
                receipt,
                self.ROOM,
                self.BOOKING_DATE,
                self.START,
                self.END,
                timeout_seconds=timeout_seconds,
            )

    def _extension_edit_page(
        self,
        *,
        polling_error=None,
        post_save_visible_polls=0,
        legacy_fallback=False,
    ):
        page = mock.MagicMock()
        page.url = self.EVENT_URL
        page.viewport_size = None

        panel = mock.MagicMock()
        more_button = mock.MagicMock()
        more_button.first = more_button
        more_button.count.return_value = 1
        more_button.is_visible.return_value = True
        panel.first = panel
        panel.count.return_value = 1

        def panel_locator(selector):
            if selector.startswith("xpath=ancestor::"):
                return _FakeLocator()
            return more_button

        panel.locator.side_effect = panel_locator

        panels = mock.MagicMock()
        panels.all.return_value = [panel]

        edit_option = mock.MagicMock()
        edit_option.count.return_value = 1
        edit_option.is_visible.return_value = True
        menu_items = mock.MagicMock()
        menu_items.filter.return_value.first = edit_option

        end_input = mock.MagicMock()
        end_input.first = end_input
        end_input.count.return_value = 1
        end_input.is_visible.return_value = True
        end_input.input_value.return_value = self.END

        finished_editing = mock.MagicMock()
        finished_editing.first = finished_editing
        finished_editing.count.return_value = 0

        warnings = mock.MagicMock()
        warnings.all.return_value = []

        save_button = mock.MagicMock()
        save_button.count.return_value = 1
        save_button.is_visible.return_value = True
        save_icon = mock.MagicMock()
        save_icon.first = save_icon
        save_icon.count.return_value = 0
        save_button.locator.return_value = save_icon
        save_locator = mock.MagicMock()
        save_locator.first = save_button

        end_calls = 0

        def locator(selector):
            nonlocal end_calls
            if selector == ".as-event-panel":
                return panels
            if selector == "[data-cy='event_4242'], #event_4242":
                return _FakeLocator() if legacy_fallback else panel
            if selector == '[data-asimutbooker-extension-target="4242"]':
                return panel if legacy_fallback else _FakeLocator()
            if selector == "mat-list-item":
                return menu_items
            if (
                "#endDate" in selector
                and "input[aria-label='End time']" in selector
            ):
                end_calls += 1
                if end_calls == 1:
                    return end_input
                if polling_error is not None:
                    raise polling_error
                if end_calls <= 1 + post_save_visible_polls:
                    return end_input
                return finished_editing
            if selector.startswith(".warning, .error"):
                return warnings
            if selector == "button:has-text('Save')":
                return save_locator
            return _FakeLocator()

        page.locator.side_effect = locator
        page.evaluate.side_effect = [
            0,
            {
                "room": self.ROOM,
                "date": self.BOOKING_DATE.isoformat(),
                "start": self.START,
                "end": self.END,
            },
        ]
        return page

    def test_delayed_positive_url_is_reloaded_exactly_then_receipt_is_verified(self):
        receipt = self._pending_receipt()
        page = _FakePage(
            url_after_waits={3: self.EVENT_URL},
            snapshots=[
                {
                    "room": self.ROOM,
                    "date": self.BOOKING_DATE.isoformat(),
                    "start": self.START,
                    "end": None,
                },
                self.EXACT_SNAPSHOT,
            ],
        )

        result = self._wait_for_outcome(page, receipt)

        self.assertTrue(result)
        self.assertGreaterEqual(page.wait_count, 4)
        self.assertEqual(page.reload_count, 1)
        journal_receipt = load_journal(self.receipts_path)["receipts"][receipt["id"]]
        self.assertEqual(journal_receipt["status"], "verified")
        self.assertEqual(journal_receipt["event_url"], self.EVENT_URL)
        self.assertEqual(list_pending(self.receipts_path), [])

    def test_visible_explicit_rejection_resolves_receipt_without_verifying(self):
        receipt = self._pending_receipt()
        page = _FakePage(
            rejection_after_waits=2,
            rejection_text="Booking clashes with another event",
        )

        result = self._wait_for_outcome(page, receipt)

        self.assertFalse(result)
        journal_receipt = load_journal(self.receipts_path)["receipts"][receipt["id"]]
        self.assertEqual(journal_receipt["status"], "resolved")
        self.assertIn("Asimut rejected Save", journal_receipt["resolution"])
        self.assertIn("Booking clashes", journal_receipt["resolution"])
        self.assertNotIn("verified_at", journal_receipt)

    def test_timeout_keeps_receipt_pending_and_stops_as_uncertain(self):
        receipt = self._pending_receipt()
        page = _FakePage()

        with self.assertRaisesRegex(
            book_week.BookingVerificationError, "Save outcome is uncertain"
        ):
            self._wait_for_outcome(
                page,
                receipt,
                timeout_seconds=1,
                clock_step=0.4,
            )

        pending = list_pending(self.receipts_path)
        self.assertEqual([item["id"] for item in pending], [receipt["id"]])
        self.assertNotIn("event_url", pending[0])
        self.assertNotIn("verified_at", pending[0])
        self.assertNotIn("resolved_at", pending[0])

    def test_unexpected_post_save_page_error_keeps_receipt_pending_and_stops(self):
        receipt = self._pending_receipt()
        page = _FakePage()
        page.wait_for_timeout = mock.Mock(
            side_effect=RuntimeError("page transport failed after Save")
        )

        with self.assertRaisesRegex(
            book_week.BookingVerificationError,
            "Save outcome is uncertain during verification",
        ):
            self._wait_for_outcome(page, receipt)

        pending = list_pending(self.receipts_path)
        self.assertEqual([item["id"] for item in pending], [receipt["id"]])
        self.assertNotIn("verified_at", pending[0])
        self.assertNotIn("resolved_at", pending[0])

    def test_extension_post_save_page_error_is_never_returned_as_retryable(self):
        page = self._extension_edit_page(
            polling_error=RuntimeError("event editor disappeared unexpectedly")
        )
        booking = {
            "room": self.ROOM,
            "date": self.BOOKING_DATE.isoformat(),
            "startTime": self.START,
            "endTime": "10:30",
            "eventId": 4242,
            "event_url": self.EVENT_URL,
        }

        with (
            mock.patch.object(book_week, "safe_goto"),
            mock.patch.object(
                book_week,
                "record_pending_extension",
                return_value={"id": "extension-receipt"},
            ),
            self.assertRaisesRegex(
                book_week.BookingVerificationError,
                "Extension Save outcome is uncertain after the click",
            ),
        ):
            book_week.edit_reservation_end_time(page, booking, self.END)

    def test_aria_only_extension_editor_must_close_before_verification(self):
        page = self._extension_edit_page(post_save_visible_polls=2)
        booking = {
            "room": self.ROOM,
            "date": self.BOOKING_DATE.isoformat(),
            "startTime": self.START,
            "endTime": "10:30",
            "eventId": 4242,
            "event_url": self.EVENT_URL,
        }
        verification_selector_counts = []

        def verify_after_editor_closed(*_args, **_kwargs):
            selector_calls = [
                call.args[0]
                for call in page.locator.call_args_list
                if call.args
                and "#endDate" in call.args[0]
                and "input[aria-label='End time']" in call.args[0]
            ]
            verification_selector_counts.append(len(selector_calls))
            return True

        with (
            mock.patch.object(book_week, "safe_goto"),
            mock.patch.object(
                book_week,
                "record_pending_extension",
                return_value={"id": "extension-receipt"},
            ),
            mock.patch.object(
                book_week,
                "verify_persisted_booking_page",
                side_effect=verify_after_editor_closed,
            ),
            mock.patch.object(book_week, "verify_mutation_receipt"),
        ):
            result = book_week.edit_reservation_end_time(page, booking, self.END)

        self.assertTrue(result)
        self.assertEqual(verification_selector_counts, [4])

    def test_verified_extension_returns_success_before_cleanup_navigation(self):
        page = self._extension_edit_page()
        booking = {
            "room": self.ROOM,
            "date": self.BOOKING_DATE.isoformat(),
            "startTime": self.START,
            "endTime": "10:30",
            "eventId": 4242,
            "event_url": self.EVENT_URL,
        }

        with (
            mock.patch.object(book_week, "safe_goto") as safe_goto,
            mock.patch.object(
                book_week,
                "record_pending_extension",
                return_value={"id": "extension-receipt"},
            ),
            mock.patch.object(
                book_week,
                "verify_persisted_booking_page",
                return_value=True,
            ) as verify_page,
            mock.patch.object(book_week, "verify_mutation_receipt") as verify_receipt,
        ):
            result = book_week.edit_reservation_end_time(page, booking, self.END)

        self.assertTrue(result)
        safe_goto.assert_called_once_with(page, book_week.ASIMUT_AGENDA_URL)
        verify_page.assert_called_once_with(
            page,
            self.ROOM,
            self.BOOKING_DATE.isoformat(),
            self.START,
            self.END,
        )
        verify_receipt.assert_called_once_with(
            "extension-receipt",
            event_url=self.EVENT_URL,
        )
        match_script = page.evaluate.call_args_list[0].args[0]
        self.assertIn('[data-cy="event_${eventId}"]', match_script)
        self.assertIn("if (panels.length !== 1) return -2", match_script)
        self.assertIn("endTime !== currentEnd", match_script)
        self.assertIn(r"\s*[-–]\s*", match_script)
        self.assertIn(
            mock.call("[data-cy='event_4242'], #event_4242"),
            page.locator.call_args_list,
        )

    def test_legacy_extension_reselection_uses_only_proven_canonical_panel(self):
        page = self._extension_edit_page(legacy_fallback=True)
        booking = {
            "room": self.ROOM,
            "date": self.BOOKING_DATE.isoformat(),
            "startTime": self.START,
            "endTime": "10:30",
            "eventId": 4242,
            "event_url": self.EVENT_URL,
        }

        with (
            mock.patch.object(book_week, "safe_goto"),
            mock.patch.object(
                book_week,
                "record_pending_extension",
                return_value={"id": "extension-receipt"},
            ),
            mock.patch.object(
                book_week,
                "verify_persisted_booking_page",
                return_value=True,
            ),
            mock.patch.object(book_week, "verify_mutation_receipt"),
        ):
            result = book_week.edit_reservation_end_time(page, booking, self.END)

        self.assertTrue(result)
        match_script = page.evaluate.call_args_list[0].args[0]
        self.assertIn("url.origin === window.location.origin", match_script)
        self.assertIn("url.pathname === '/arrangement'", match_script)
        self.assertIn("url.search === `?eventId=${eventId}`", match_script)
        self.assertIn(
            "panel.setAttribute(targetMarker, String(eventId))",
            match_script,
        )
        self.assertIn(
            mock.call('[data-asimutbooker-extension-target="4242"]'),
            page.locator.call_args_list,
        )
        self.assertFalse(
            any(
                call.args and "href$=" in call.args[0]
                for call in page.locator.call_args_list
            )
        )

    def test_legacy_extension_identity_is_persisted_before_editing(self):
        settings_path = Path(self.temporary_directory.name) / "settings.json"
        legacy = {
            "room": self.ROOM,
            "date": self.BOOKING_DATE.isoformat(),
            "startTime": self.START,
            "endTime": "10:30",
            "target_end": self.END,
            "created_at": datetime.now().isoformat(),
            "horizon_days": 5,
        }
        reservation = {
            "room": self.ROOM,
            "date": self.BOOKING_DATE.isoformat(),
            "startTime": self.START,
            "endTime": "10:45",
            "eventId": 4242,
        }
        book_week.update_settings(
            lambda settings: settings.update(
                {"extendable_bookings": [dict(legacy)]}
            ),
            settings_path,
        )

        with mock.patch.object(book_week, "settings_file", settings_path):
            migrated = book_week.ensure_extendable_booking_event_identity(
                legacy,
                [reservation],
            )
            persisted = book_week.load_settings_document(settings_path)[
                "extendable_bookings"
            ][0]

        self.assertEqual(migrated["eventId"], 4242)
        self.assertEqual(migrated["event_url"], self.EVENT_URL)
        self.assertEqual(migrated["endTime"], "10:45")
        self.assertEqual(persisted["eventId"], 4242)
        self.assertEqual(persisted["event_url"], self.EVENT_URL)
        self.assertEqual(persisted["endTime"], "10:45")

    def test_new_extendable_state_carries_verified_event_identity(self):
        settings_path = Path(self.temporary_directory.name) / "settings.json"

        with mock.patch.object(book_week, "settings_file", settings_path):
            book_week.save_extendable_booking(
                self.ROOM,
                self.BOOKING_DATE,
                9.5,
                10.0,
                11.0,
                event_url=self.EVENT_URL,
            )
            saved = book_week.load_extendable_bookings()[0]

        self.assertEqual(saved["eventId"], 4242)
        self.assertEqual(saved["event_url"], self.EVENT_URL)

    def test_save_extendable_booking_rejects_duplicate_local_tuple(self):
        settings_path = Path(self.temporary_directory.name) / "settings.json"

        with mock.patch.object(book_week, "settings_file", settings_path):
            book_week.save_extendable_booking(
                self.ROOM,
                self.BOOKING_DATE,
                9.5,
                10.0,
                11.0,
                event_url=self.EVENT_URL,
            )
            book_week.update_settings(
                lambda settings: settings["extendable_bookings"].append(
                    dict(settings["extendable_bookings"][0])
                ),
                settings_path,
            )

            with self.assertRaisesRegex(
                book_week.SettingsError,
                "Duplicate extendable booking state",
            ):
                book_week.save_extendable_booking(
                    self.ROOM,
                    self.BOOKING_DATE,
                    9.5,
                    10.0,
                    11.0,
                    event_url=self.EVENT_URL,
                )

            persisted = book_week.load_settings_document(settings_path)[
                "extendable_bookings"
            ]

        self.assertEqual(len(persisted), 2)

    def test_save_extendable_booking_rejects_changed_event_identity(self):
        settings_path = Path(self.temporary_directory.name) / "settings.json"
        changed_url = "https://rwcmd.asimut.net/arrangement?eventId=4243"

        with mock.patch.object(book_week, "settings_file", settings_path):
            book_week.save_extendable_booking(
                self.ROOM,
                self.BOOKING_DATE,
                9.5,
                10.0,
                11.0,
                event_url=changed_url,
            )

            with self.assertRaisesRegex(
                book_week.SettingsError,
                "different or unverified event identity",
            ):
                book_week.save_extendable_booking(
                    self.ROOM,
                    self.BOOKING_DATE,
                    9.5,
                    10.0,
                    11.0,
                    event_url=self.EVENT_URL,
                )

            persisted = book_week.load_settings_document(settings_path)[
                "extendable_bookings"
            ][0]

        self.assertEqual(persisted["eventId"], 4243)
        self.assertEqual(persisted["event_url"], changed_url)

    def test_legacy_migration_rejects_duplicate_or_changed_local_state(self):
        settings_path = Path(self.temporary_directory.name) / "settings.json"
        legacy = {
            "room": self.ROOM,
            "date": self.BOOKING_DATE.isoformat(),
            "startTime": self.START,
            "endTime": "10:30",
            "target_end": self.END,
            "created_at": datetime.now().isoformat(),
            "horizon_days": 5,
        }
        reservation = {
            "room": self.ROOM,
            "date": self.BOOKING_DATE.isoformat(),
            "startTime": self.START,
            "endTime": "10:45",
            "eventId": 4242,
        }

        with mock.patch.object(book_week, "settings_file", settings_path):
            book_week.update_settings(
                lambda settings: settings.update(
                    {"extendable_bookings": [dict(legacy), dict(legacy)]}
                ),
                settings_path,
            )
            with self.assertRaisesRegex(
                book_week.SettingsError,
                "state changed during identity migration",
            ):
                book_week.ensure_extendable_booking_event_identity(
                    legacy,
                    [reservation],
                )

            changed = dict(legacy, target_end="11:15")
            book_week.update_settings(
                lambda settings: settings.update(
                    {"extendable_bookings": [changed]}
                ),
                settings_path,
            )
            with self.assertRaisesRegex(
                book_week.SettingsError,
                "state changed during identity migration",
            ):
                book_week.ensure_extendable_booking_event_identity(
                    legacy,
                    [reservation],
                )

            persisted = book_week.load_settings_document(settings_path)[
                "extendable_bookings"
            ][0]

        self.assertNotIn("eventId", persisted)
        self.assertEqual(persisted["target_end"], "11:15")

    def test_legacy_extension_identity_ambiguity_fails_before_persistence(self):
        legacy = {
            "room": self.ROOM,
            "date": self.BOOKING_DATE.isoformat(),
            "startTime": self.START,
            "endTime": "10:30",
            "target_end": self.END,
            "created_at": datetime.now().isoformat(),
            "horizon_days": 5,
        }
        reservations = [
            {
                "room": self.ROOM,
                "date": self.BOOKING_DATE.isoformat(),
                "startTime": self.START,
                "endTime": "10:30",
                "eventId": event_id,
            }
            for event_id in (4242, 4243)
        ]

        with (
            mock.patch.object(book_week, "update_settings") as update,
            self.assertRaisesRegex(book_week.SettingsError, "exactly one is required"),
        ):
            book_week.ensure_extendable_booking_event_identity(
                legacy,
                reservations,
            )

        update.assert_not_called()

    def test_positive_url_with_wrong_persisted_identity_keeps_receipt_pending(self):
        receipt = self._pending_receipt()
        page = _FakePage(
            url_after_waits={1: self.EVENT_URL},
            snapshots=[{
                "room": "B0.27",
                "date": self.BOOKING_DATE.isoformat(),
                "start": self.START,
                "end": self.END,
            }],
        )

        with self.assertRaisesRegex(
            book_week.BookingVerificationError, "persisted room/date/time did not match"
        ):
            self._wait_for_outcome(page, receipt, clock_step=0.5)

        pending = list_pending(self.receipts_path)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "pending")
        self.assertEqual(pending[0]["event_url"], self.EVENT_URL)
        self.assertNotIn("verified_at", pending[0])
        self.assertNotIn("resolved_at", pending[0])

    def test_unrelated_body_text_cannot_verify_wrong_persisted_record(self):
        receipt = self._pending_receipt()
        page = _FakePage(
            url_after_waits={1: self.EVENT_URL},
            snapshots=[{
                "room": "B1.09",
                "date": "2026-09-01",
                "start": "10:00",
                "end": "10:30",
                "global_body_text": (
                    "Reservation B0.29 Monday 31 August 2026 09:30-11:00"
                ),
            }],
        )

        with self.assertRaisesRegex(
            book_week.BookingVerificationError, "persisted room/date/time did not match"
        ):
            self._wait_for_outcome(page, receipt, clock_step=0.5)

        pending = list_pending(self.receipts_path)
        self.assertEqual([item["id"] for item in pending], [receipt["id"]])
        self.assertEqual(pending[0]["event_url"], self.EVENT_URL)

    def test_snapshot_extractor_does_not_read_global_document_body(self):
        page = _SnapshotScriptPage(self.EXACT_SNAPSHOT)

        self.assertEqual(book_week.page_booking_snapshot(page), self.EXACT_SNAPSHOT)
        self.assertIsInstance(page.script, str)
        self.assertNotIn("document.body", page.script)

    def test_snapshot_extractor_supports_current_arrangement_event_card(self):
        page = _SnapshotScriptPage(self.EXACT_SNAPSHOT)

        self.assertEqual(book_week.page_booking_snapshot(page), self.EXACT_SNAPSHOT)
        self.assertIn("event_${expectedEventId}", page.script)
        self.assertIn("numericShortYear", page.script)

    def test_expired_pending_receipt_cannot_jam_future_autonomous_runs(self):
        expired_day = (datetime.now() - timedelta(days=1)).date().isoformat()
        receipt = {
            "id": "expired-receipt",
            "room": self.ROOM,
            "date": expired_day,
            "start": "09:30",
            "end": "11:00",
        }
        page = mock.Mock()

        with mock.patch.object(
            book_week, "list_pending_mutation_receipts", return_value=[receipt]
        ), mock.patch.object(book_week, "resolve_mutation_receipt") as resolve:
            book_week.reconcile_pending_mutation_receipts(page, [])

        resolve.assert_called_once_with(
            receipt["id"],
            resolution="Interrupted mutation expired after its booking slot ended",
            event_url=None,
        )
        page.assert_not_called()

    def test_persisted_event_url_must_remain_exactly_stable_across_reload(self):
        page = _FakePage(
            url=self.EVENT_URL,
            reload_url="https://rwcmd.asimut.net/arrangement?eventId=4243",
            snapshots=[self.EXACT_SNAPSHOT],
        )

        with self.assertRaisesRegex(
            book_week.BookingVerificationError, "persisted event URL changed"
        ):
            book_week.verify_persisted_booking_page(
                page,
                self.ROOM,
                self.BOOKING_DATE,
                self.START,
                self.END,
            )

    def test_reconciliation_cannot_verify_a_receipt_after_event_url_redirect(self):
        future_date = (datetime.now() + timedelta(days=1)).date().isoformat()
        receipt = {
            "id": "redirected-receipt",
            "room": self.ROOM,
            "date": future_date,
            "start": self.START,
            "end": self.END,
            "event_url": self.EVENT_URL,
        }
        page = _FakePage(url=book_week.ASIMUT_AGENDA_URL)

        def redirected_goto(target_page, _url):
            target_page.url = book_week.ASIMUT_AGENDA_URL

        with (
            mock.patch.object(
                book_week,
                "list_pending_mutation_receipts",
                return_value=[receipt],
            ),
            mock.patch.object(book_week, "safe_goto", side_effect=redirected_goto),
            mock.patch.object(book_week, "verify_mutation_receipt") as verify_receipt,
            self.assertRaisesRegex(
                book_week.BookingVerificationError,
                "exact positive arrangement event URL",
            ),
        ):
            book_week.reconcile_pending_mutation_receipts(page, [])

        verify_receipt.assert_not_called()

    def test_reconciliation_prefers_recorded_event_url_over_agenda_match(self):
        future_date = (datetime.now() + timedelta(days=1)).date().isoformat()
        receipt = {
            "id": "url-first-receipt",
            "room": self.ROOM,
            "date": future_date,
            "start": self.START,
            "end": self.END,
            "event_url": self.EVENT_URL,
        }
        exact_agenda_match = [{
            "room": self.ROOM,
            "date": future_date,
            "startTime": self.START,
            "endTime": self.END,
        }]
        page = _FakePage(
            url=book_week.ASIMUT_AGENDA_URL,
            snapshots=[{
                "room": "B0.27",
                "date": future_date,
                "start": self.START,
                "end": self.END,
            }],
        )

        def goto_recorded_event(target_page, url):
            self.assertEqual(url, self.EVENT_URL)
            target_page.url = url

        with (
            mock.patch.object(
                book_week,
                "list_pending_mutation_receipts",
                return_value=[receipt],
            ),
            mock.patch.object(book_week, "safe_goto", side_effect=goto_recorded_event),
            mock.patch.object(book_week, "verify_mutation_receipt") as verify_receipt,
            self.assertRaisesRegex(
                book_week.BookingVerificationError,
                "persisted room/date/time did not match",
            ),
        ):
            book_week.reconcile_pending_mutation_receipts(page, exact_agenda_match)

        verify_receipt.assert_not_called()

    def test_reconciliation_rejects_noncanonical_recorded_event_url(self):
        future_date = (datetime.now() + timedelta(days=1)).date().isoformat()
        receipt = {
            "id": "invalid-url-receipt",
            "room": self.ROOM,
            "date": future_date,
            "start": self.START,
            "end": self.END,
            "event_url": "https://rwcmd.asimut.net/event?eventId=4242",
        }
        reservation = {
            "room": self.ROOM,
            "date": future_date,
            "startTime": self.START,
            "endTime": self.END,
            "eventId": 4242,
        }
        page = _FakePage(url=book_week.ASIMUT_AGENDA_URL)

        with (
            mock.patch.object(
                book_week,
                "list_pending_mutation_receipts",
                return_value=[receipt],
            ),
            mock.patch.object(book_week, "safe_goto") as safe_goto,
            mock.patch.object(book_week, "verify_mutation_receipt") as verify_receipt,
            self.assertRaisesRegex(
                book_week.BookingVerificationError,
                "noncanonical event URL",
            ),
        ):
            book_week.reconcile_pending_mutation_receipts(page, [reservation])

        safe_goto.assert_not_called()
        verify_receipt.assert_not_called()

    def test_expired_receipt_with_noncanonical_url_remains_pending(self):
        expired_date = (datetime.now() - timedelta(days=1)).date().isoformat()
        receipt = {
            "id": "expired-invalid-url-receipt",
            "room": self.ROOM,
            "date": expired_date,
            "start": self.START,
            "end": self.END,
            "event_url": "https://rwcmd.asimut.net/event?eventId=4242",
        }
        page = _FakePage(url=book_week.ASIMUT_AGENDA_URL)

        with (
            mock.patch.object(
                book_week,
                "list_pending_mutation_receipts",
                return_value=[receipt],
            ),
            mock.patch.object(book_week, "resolve_mutation_receipt") as resolve_receipt,
            self.assertRaisesRegex(
                book_week.BookingVerificationError,
                "noncanonical event URL",
            ),
        ):
            book_week.reconcile_pending_mutation_receipts(page, [])

        resolve_receipt.assert_not_called()

    def test_reconciliation_derives_and_reloads_unique_positive_agenda_event(self):
        future_date = (datetime.now() + timedelta(days=1)).date().isoformat()
        receipt = {
            "id": "agenda-derived-receipt",
            "room": self.ROOM,
            "date": future_date,
            "start": self.START,
            "end": self.END,
        }
        reservation = {
            "room": self.ROOM,
            "date": future_date,
            "startTime": self.START,
            "endTime": self.END,
            "eventId": 4242,
        }
        page = _FakePage(
            url=book_week.ASIMUT_AGENDA_URL,
            snapshots=[{
                "room": self.ROOM,
                "date": future_date,
                "start": self.START,
                "end": self.END,
            }],
        )

        def goto_derived_event(target_page, url):
            self.assertEqual(url, self.EVENT_URL)
            target_page.url = url

        with (
            mock.patch.object(
                book_week,
                "list_pending_mutation_receipts",
                return_value=[receipt],
            ),
            mock.patch.object(book_week, "safe_goto", side_effect=goto_derived_event),
            mock.patch.object(book_week, "verify_mutation_receipt") as verify_receipt,
        ):
            book_week.reconcile_pending_mutation_receipts(page, [reservation])

        self.assertEqual(page.reload_count, 1)
        verify_receipt.assert_called_once_with(
            receipt["id"], event_url=self.EVENT_URL
        )

    def test_reconciliation_cannot_derive_event_url_without_positive_agenda_id(self):
        future_date = (datetime.now() + timedelta(days=1)).date().isoformat()
        receipt = {
            "id": "missing-agenda-id-receipt",
            "room": self.ROOM,
            "date": future_date,
            "start": self.START,
            "end": self.END,
        }
        reservation = {
            "room": self.ROOM,
            "date": future_date,
            "startTime": self.START,
            "endTime": self.END,
            "eventId": None,
        }
        page = _FakePage(url=book_week.ASIMUT_AGENDA_URL)

        with (
            mock.patch.object(
                book_week,
                "list_pending_mutation_receipts",
                return_value=[receipt],
            ),
            mock.patch.object(book_week, "safe_goto") as safe_goto,
            mock.patch.object(book_week, "verify_mutation_receipt") as verify_receipt,
            self.assertRaisesRegex(
                book_week.BookingVerificationError,
                "positive event id",
            ),
        ):
            book_week.reconcile_pending_mutation_receipts(page, [reservation])

        safe_goto.assert_not_called()
        verify_receipt.assert_not_called()

    def test_reconciliation_requires_same_event_id_on_page_and_agenda(self):
        future_date = (datetime.now() + timedelta(days=1)).date().isoformat()
        receipt = {
            "id": "dual-proof-receipt",
            "room": self.ROOM,
            "date": future_date,
            "start": self.START,
            "end": self.END,
            "event_url": self.EVENT_URL,
        }
        reservation = {
            "room": self.ROOM,
            "date": future_date,
            "startTime": self.START,
            "endTime": self.END,
            "eventId": 4242,
        }
        page = _FakePage(
            url=book_week.ASIMUT_AGENDA_URL,
            snapshots=[{
                "room": self.ROOM,
                "date": future_date,
                "start": self.START,
                "end": self.END,
            }],
        )

        def goto_recorded_event(target_page, url):
            target_page.url = url

        with (
            mock.patch.object(
                book_week,
                "list_pending_mutation_receipts",
                return_value=[receipt],
            ),
            mock.patch.object(book_week, "safe_goto", side_effect=goto_recorded_event),
            mock.patch.object(book_week, "verify_mutation_receipt") as verify_receipt,
        ):
            book_week.reconcile_pending_mutation_receipts(page, [reservation])

        verify_receipt.assert_called_once_with(
            receipt["id"], event_url=self.EVENT_URL
        )

    def test_reconciliation_rejects_wrong_agenda_event_id_after_exact_page(self):
        future_date = (datetime.now() + timedelta(days=1)).date().isoformat()
        receipt = {
            "id": "wrong-agenda-id-receipt",
            "room": self.ROOM,
            "date": future_date,
            "start": self.START,
            "end": self.END,
            "event_url": self.EVENT_URL,
        }
        reservation = {
            "room": self.ROOM,
            "date": future_date,
            "startTime": self.START,
            "endTime": self.END,
            "eventId": 4243,
        }
        page = _FakePage(
            url=book_week.ASIMUT_AGENDA_URL,
            snapshots=[{
                "room": self.ROOM,
                "date": future_date,
                "start": self.START,
                "end": self.END,
            }],
        )

        def goto_recorded_event(target_page, url):
            target_page.url = url

        with (
            mock.patch.object(
                book_week,
                "list_pending_mutation_receipts",
                return_value=[receipt],
            ),
            mock.patch.object(book_week, "safe_goto", side_effect=goto_recorded_event),
            mock.patch.object(book_week, "verify_mutation_receipt") as verify_receipt,
            self.assertRaisesRegex(
                book_week.BookingVerificationError,
                "reported event id 4243",
            ),
        ):
            book_week.reconcile_pending_mutation_receipts(page, [reservation])

        verify_receipt.assert_not_called()

    def test_pre_and_post_identity_checks_reject_near_matches(self):
        exact_identity = {
            "room": self.ROOM,
            "date": self.BOOKING_DATE.isoformat(),
        }
        self.assertTrue(
            book_week.booking_identity_matches(
                exact_identity, self.ROOM, self.BOOKING_DATE
            )
        )
        self.assertTrue(
            book_week.booking_times_match(
                self.START, self.END, self.START, self.END
            )
        )

        for snapshot, room, booking_date in (
            ({"room": "B0.290", "date": self.BOOKING_DATE.isoformat()}, self.ROOM, self.BOOKING_DATE),
            (exact_identity, "B0.27", self.BOOKING_DATE),
            (exact_identity, self.ROOM, date(2026, 9, 1)),
        ):
            with self.subTest(snapshot=snapshot, room=room, booking_date=booking_date):
                self.assertFalse(
                    book_week.booking_identity_matches(snapshot, room, booking_date)
                )

        self.assertFalse(
            book_week.booking_times_match(
                self.START, self.END, self.START, "10:45"
            )
        )
        self.assertFalse(
            book_week.booking_times_match(
                self.START, self.END, "09:45", self.END
            )
        )

    def test_nonpositive_event_url_is_never_treated_as_persisted(self):
        page = _FakePage(
            url="https://rwcmd.asimut.net/arrangement?eventId=0",
            snapshots=[self.EXACT_SNAPSHOT],
        )

        with self.assertRaisesRegex(
            book_week.BookingVerificationError,
            "exact positive arrangement event URL",
        ):
            book_week.verify_persisted_booking_page(
                page,
                self.ROOM,
                self.BOOKING_DATE,
                self.START,
                self.END,
            )
        self.assertEqual(page.reload_count, 0)


if __name__ == "__main__":
    unittest.main()
