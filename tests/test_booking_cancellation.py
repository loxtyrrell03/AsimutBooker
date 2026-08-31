import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import book_week
from app_settings import SettingsError
from mutation_receipts import MutationReceiptError
from playwright.sync_api import sync_playwright


class _Locator:
    def __init__(self, items=()):
        self.items = list(items)

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]

    @property
    def first(self):
        return self.items[0]


class _MenuOption:
    def __init__(
        self,
        text,
        *,
        visible=True,
        enabled=True,
        attributes=None,
        icons=(),
        tag_name="mat-list-item",
    ):
        self.text = text
        self.visible = visible
        self.enabled = enabled
        self.attributes = attributes or {}
        self.icons = tuple(_MenuOption(icon) for icon in icons)
        self.tag_name = tag_name

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def inner_text(self):
        return self.text

    def text_content(self):
        return self.text

    def get_attribute(self, name):
        return self.attributes.get(name)

    def locator(self, selector):
        if selector == "mat-icon":
            return _Locator(self.icons)
        return _Locator()

    def evaluate(self, _script):
        return self.tag_name


class _MenuPage:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def locator(self, selector):
        return _Locator(self.mapping.get(selector, ()))


_OVERLAY_SELECTOR = ".cdk-overlay-pane:visible"
_CANCELLATION_OPTION_SELECTOR = (
    "[role='menuitem'], button[mat-menu-item], mat-list-item, "
    ".mat-menu-item, .mat-mdc-menu-item"
)


def _menu_page(*options, overlay_count=1):
    overlays = tuple(
        _MenuPage({_CANCELLATION_OPTION_SELECTOR: options})
        for _index in range(overlay_count)
    )
    return _MenuPage({_OVERLAY_SELECTOR: overlays})


class BookingCancellationContractTests(unittest.TestCase):
    EVENT_ID = 4242
    EVENT_URL = "https://rwcmd.asimut.net/arrangement?eventId=4242"
    ROOM = "B0.29"
    DATE = "2026-09-02"
    START = "16:00"
    END = "16:30"

    def reservation(self, **updates):
        value = {
            "eventId": self.EVENT_ID,
            "room": self.ROOM,
            "date": self.DATE,
            "startTime": self.START,
            "endTime": self.END,
            "daysDiff": 2,
        }
        value.update(updates)
        return value

    def receipt(self, **updates):
        value = {
            "id": "cancel-receipt",
            "kind": "cancel",
            "status": "pending",
            "room": self.ROOM,
            "date": self.DATE,
            "start": self.START,
            "end": self.END,
            "event_url": self.EVENT_URL,
        }
        value.update(updates)
        return value

    def call_cancel(self, *, post_reservations=(), record_side_effect=None,
                    cancel_side_effect=None, confirmation=True,
                    scan_side_effects=None, post_agenda_events=None):
        page = mock.Mock()
        card = mock.Mock()
        more_button = mock.Mock()
        cancel_option = mock.Mock()
        confirm_button = mock.Mock() if confirmation else None
        timeline = []

        more_button.click.side_effect = lambda *args, **kwargs: timeline.append("menu")
        if cancel_side_effect is None:
            cancel_option.click.side_effect = (
                lambda *args, **kwargs: timeline.append("cancel_click")
            )
        else:
            cancel_option.click.side_effect = cancel_side_effect
        if confirm_button is not None:
            confirm_button.click.side_effect = (
                lambda *args, **kwargs: timeline.append("confirm_click")
            )

        receipt = self.receipt()

        def record_receipt(**kwargs):
            timeline.append("receipt")
            if record_side_effect is not None:
                raise record_side_effect
            self.assertEqual(kwargs["event_url"], self.EVENT_URL)
            return receipt

        queued_scan_side_effects = list(scan_side_effects or ())

        def complete_scan(*args, **kwargs):
            timeline.append("complete_agenda")
            self.assertEqual(kwargs["snapshot_path"], book_week.AGENDA_SNAPSHOT_FILE)
            if queued_scan_side_effects:
                result = queued_scan_side_effects.pop(0)
                if isinstance(result, BaseException):
                    raise result
                args[1].agenda_events = list(
                    post_agenda_events
                    if post_agenda_events is not None
                    else result[1]
                )
                return result
            args[1].agenda_events = list(
                post_agenda_events
                if post_agenda_events is not None
                else post_reservations
            )
            return 3, list(post_reservations)

        def verify_receipt(*args, **kwargs):
            timeline.append("verified")

        stack = ExitStack()
        stack.enter_context(mock.patch.object(book_week, "safe_goto"))
        stack.enter_context(mock.patch.object(book_week, "verify_persisted_booking_page"))
        stack.enter_context(
            mock.patch.object(book_week, "_find_exact_cancellation_card", return_value=card)
        )
        stack.enter_context(
            mock.patch.object(book_week, "_unique_visible_control", return_value=more_button)
        )
        stack.enter_context(
            mock.patch.object(book_week, "_cancel_menu_option", return_value=cancel_option)
        )
        stack.enter_context(
            mock.patch.object(
                book_week,
                "_optional_cancel_confirmation",
                return_value=confirm_button,
            )
        )
        stack.enter_context(
            mock.patch.object(book_week, "record_pending_cancel", side_effect=record_receipt)
        )
        scan = stack.enter_context(
            mock.patch.object(book_week, "scan_agenda", side_effect=complete_scan)
        )
        verify = stack.enter_context(
            mock.patch.object(
                book_week,
                "verify_mutation_receipt",
                side_effect=verify_receipt,
            )
        )
        resolve = stack.enter_context(
            mock.patch.object(book_week, "resolve_mutation_receipt")
        )
        stack.enter_context(
            mock.patch.object(book_week, "remove_extendable_booking_by_event_id")
        )
        stack.enter_context(mock.patch.object(book_week, "clear_booking_plan"))

        self.last_timeline = timeline
        self.last_cancel_option = cancel_option
        self.last_scan = scan
        self.last_verify = verify
        self.last_resolve = resolve

        try:
            result = book_week.cancel_reservation_exact(
                page,
                [self.reservation()],
                event_id=self.EVENT_ID,
                room=self.ROOM,
                date_str=self.DATE,
                start_time=self.START,
                end_time=self.END,
                today=datetime(2026, 8, 31).date(),
                live_dates=(datetime(2026, 8, 31).date(),),
                ignored_events=set(),
            )
            return result, timeline, cancel_option, scan, verify, resolve
        finally:
            stack.close()

    def test_target_requires_one_exact_id_and_exact_tuple(self):
        target = book_week.resolve_exact_cancellation_target(
            [self.reservation()],
            event_id=self.EVENT_ID,
            room=self.ROOM,
            date_str=self.DATE,
            start_time=self.START,
            end_time=self.END,
        )
        self.assertEqual(target, self.reservation())

        for reservations, message in (
            ([], "matched 0"),
            ([self.reservation(), self.reservation()], "matched 2"),
            ([self.reservation(room="B0.27")], "no longer matches"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(
                book_week.BookingCancellationError, message
            ):
                book_week.resolve_exact_cancellation_target(
                    reservations,
                    event_id=self.EVENT_ID,
                    room=self.ROOM,
                    date_str=self.DATE,
                    start_time=self.START,
                    end_time=self.END,
                )

    def test_post_agenda_classifies_only_exact_absence_as_cancelled(self):
        receipt = self.receipt()
        self.assertEqual(
            book_week.classify_cancellation_agenda_outcome(
                [self.reservation()], receipt
            ),
            "not_applied",
        )
        self.assertEqual(
            book_week.classify_cancellation_agenda_outcome([], receipt),
            "cancelled",
        )
        self.assertEqual(
            book_week.classify_cancellation_agenda_outcome(
                [self.reservation(title="Class", isReservation=False)],
                receipt,
            ),
            "not_applied",
        )

        ambiguous_outcomes = (
            [self.reservation(), self.reservation()],
            [self.reservation(room="B0.27")],
            [self.reservation(eventId=4343)],
        )
        for reservations in ambiguous_outcomes:
            with self.subTest(reservations=reservations), self.assertRaises(
                book_week.BookingVerificationError
            ):
                book_week.classify_cancellation_agenda_outcome(
                    reservations, receipt
                )

    def test_receipt_precedes_every_destructive_click_and_complete_agenda_proves_result(self):
        result, timeline, _cancel, scan, verify, resolve = self.call_cancel()

        self.assertEqual(result, (True, 3))
        self.assertLess(timeline.index("receipt"), timeline.index("cancel_click"))
        self.assertLess(timeline.index("receipt"), timeline.index("confirm_click"))
        self.assertLess(timeline.index("confirm_click"), timeline.index("complete_agenda"))
        self.assertLess(timeline.index("complete_agenda"), timeline.index("verified"))
        scan.assert_called_once()
        verify.assert_called_once_with("cancel-receipt", event_url=self.EVENT_URL)
        resolve.assert_not_called()

    def test_receipt_failure_stops_before_destructive_click(self):
        with self.assertRaisesRegex(
            book_week.BookingCancellationError, "before the destructive control"
        ):
            self.call_cancel(
                record_side_effect=MutationReceiptError("disk unavailable")
            )
        self.last_cancel_option.click.assert_not_called()
        self.last_scan.assert_not_called()
        self.last_verify.assert_not_called()
        self.last_resolve.assert_not_called()

    def test_click_failure_is_uncertain_and_never_scans_or_verifies(self):
        with self.assertRaisesRegex(
            book_week.BookingVerificationError, "outcome is uncertain"
        ):
            self.call_cancel(cancel_side_effect=RuntimeError("connection lost"))
        self.last_scan.assert_not_called()
        self.last_verify.assert_not_called()

    def test_unchanged_immediate_agenda_keeps_receipt_pending_for_later_reconciliation(self):
        with self.assertRaisesRegex(
            book_week.BookingVerificationError, "delayed cancellation"
        ):
            self.call_cancel(
                post_reservations=(self.reservation(),),
                confirmation=False,
            )
        self.assertIn("complete_agenda", self.last_timeline)
        self.last_scan.assert_called_once()
        self.last_verify.assert_not_called()

    def test_transient_post_click_scan_retries_proof_without_repeating_click(self):
        result, timeline, cancel, scan, verify, _resolve = self.call_cancel(
            scan_side_effects=(
                RuntimeError("agenda rerender in progress"),
                (3, []),
            )
        )

        self.assertEqual(result, (True, 3))
        self.assertEqual(timeline.count("receipt"), 1)
        self.assertEqual(timeline.count("cancel_click"), 1)
        self.assertEqual(timeline.count("complete_agenda"), 2)
        cancel.click.assert_called_once()
        self.assertEqual(scan.call_count, 2)
        verify.assert_called_once_with("cancel-receipt", event_url=self.EVENT_URL)

    def test_repeated_post_click_scan_failure_keeps_receipt_pending(self):
        with self.assertRaisesRegex(
            book_week.BookingVerificationError,
            "requires reconciliation",
        ):
            self.call_cancel(
                scan_side_effects=(
                    RuntimeError("first unstable snapshot"),
                    RuntimeError("second unstable snapshot"),
                )
            )

        self.assertEqual(self.last_timeline.count("receipt"), 1)
        self.assertEqual(self.last_timeline.count("cancel_click"), 1)
        self.assertEqual(self.last_timeline.count("complete_agenda"), 2)
        self.last_cancel_option.click.assert_called_once()
        self.assertEqual(self.last_scan.call_count, 2)
        self.last_verify.assert_not_called()
        self.last_resolve.assert_not_called()

    def test_non_reservation_same_id_cannot_be_mistaken_for_absence(self):
        with self.assertRaisesRegex(
            book_week.BookingVerificationError,
            "delayed cancellation",
        ):
            self.call_cancel(
                post_reservations=(),
                post_agenda_events=(
                    self.reservation(title="Class", isReservation=False),
                ),
            )

        self.last_cancel_option.click.assert_called_once()
        self.last_scan.assert_called_once()
        self.last_verify.assert_not_called()

    def test_menu_resolution_rejects_ambiguous_or_generic_controls(self):
        ambiguous = _menu_page(
            _MenuOption("Cancel booking"),
            _MenuOption("Cancel booking"),
        )
        with self.assertRaisesRegex(
            book_week.BookingCancellationError, "found 2"
        ):
            book_week._cancel_menu_option(ambiguous)

        generic = _menu_page(_MenuOption("Cancel"))
        with self.assertRaisesRegex(
            book_week.BookingCancellationError, "found 0"
        ):
            book_week._cancel_menu_option(generic)

    def test_menu_resolution_accepts_exact_event_and_accessible_labels(self):
        native_event_item = _MenuOption(
            "cancel\nCancel event",
            icons=("cancel",),
        )
        page = _menu_page(native_event_item)
        self.assertIs(book_week._cancel_menu_option(page), native_event_item)

        accessible_item = _MenuOption(
            "event_busy",
            attributes={"aria-label": "Cancel reservation"},
            icons=("event_busy",),
            tag_name="button",
        )
        page = _menu_page(accessible_item)
        self.assertIs(book_week._cancel_menu_option(page), accessible_item)

        native_generic_item = _MenuOption(
            "cancel Cancel",
            icons=("cancel",),
        )
        page = _menu_page(native_generic_item)
        self.assertIs(book_week._cancel_menu_option(page), native_generic_item)

    def test_menu_resolution_rejects_elliptical_or_negated_event_text(self):
        for label in (
            "Cancel",
            "Do not cancel event",
            "Cancel event details",
            "Cancellation policy",
            "Delete event",
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                book_week.BookingCancellationError, "found 0"
            ):
                book_week._cancel_menu_option(
                    _menu_page(_MenuOption(label))
                )

    def test_generic_cancel_requires_exact_icon_item_and_unique_overlay(self):
        rejected = (
            _MenuOption("Cancel"),
            _MenuOption("close Cancel", icons=("close",)),
            _MenuOption("cancel Cancel", icons=("cancel", "warning")),
            _MenuOption(
                "cancel Cancel",
                icons=("cancel",),
                tag_name="button",
            ),
        )
        for option in rejected:
            with self.subTest(option=option), self.assertRaisesRegex(
                book_week.BookingCancellationError, "found 0"
            ):
                book_week._cancel_menu_option(_menu_page(option))

        for overlay_count in (0, 2):
            with self.subTest(overlay_count=overlay_count), self.assertRaisesRegex(
                book_week.BookingCancellationError,
                "visible event-options overlay",
            ):
                book_week._cancel_menu_option(
                    _menu_page(
                        _MenuOption("cancel Cancel", icons=("cancel",)),
                        overlay_count=overlay_count,
                    )
                )

    def test_reconciliation_proves_absent_or_unchanged_cancellation(self):
        future_date = (datetime.now() + timedelta(days=2)).date().isoformat()
        receipt = self.receipt(date=future_date)

        with (
            mock.patch.object(
                book_week,
                "list_pending_mutation_receipts",
                return_value=[receipt],
            ),
            mock.patch.object(book_week, "verify_mutation_receipt") as verified,
            mock.patch.object(book_week, "resolve_mutation_receipt") as resolved,
            mock.patch.object(book_week, "remove_extendable_booking_by_event_id") as cleanup,
            mock.patch.object(book_week, "clear_booking_plan") as clear_plan,
        ):
            book_week.reconcile_pending_mutation_receipts(mock.Mock(), [])

        verified.assert_called_once_with("cancel-receipt", event_url=self.EVENT_URL)
        resolved.assert_not_called()
        cleanup.assert_called_once_with(self.EVENT_ID)
        clear_plan.assert_called_once_with()

        remaining = self.reservation(date=future_date)
        with (
            mock.patch.object(
                book_week,
                "list_pending_mutation_receipts",
                return_value=[receipt],
            ),
            mock.patch.object(book_week, "safe_goto") as safe_goto,
            mock.patch.object(book_week, "verify_persisted_booking_page") as page_proof,
            mock.patch.object(book_week, "verify_mutation_receipt") as verified,
            mock.patch.object(book_week, "resolve_mutation_receipt") as resolved,
        ):
            book_week.reconcile_pending_mutation_receipts(
                mock.Mock(), [remaining]
            )

        safe_goto.assert_called_once_with(mock.ANY, self.EVENT_URL)
        page_proof.assert_called_once_with(
            mock.ANY, self.ROOM, future_date, self.START, self.END
        )
        verified.assert_not_called()
        resolved.assert_called_once_with(
            "cancel-receipt",
            resolution=(
                "Interrupted cancellation was not applied; exact reservation remains"
            ),
            event_url=self.EVENT_URL,
        )

    def test_extension_cleanup_is_bound_only_to_exact_event_id(self):
        settings = {
            "extendable_bookings": [
                {"eventId": self.EVENT_ID, "room": self.ROOM},
                {"eventId": 4343, "room": "B0.27"},
            ]
        }

        def update(mutator, _path):
            mutator(settings)

        with mock.patch.object(book_week, "update_settings", side_effect=update):
            book_week.remove_extendable_booking_by_event_id(self.EVENT_ID)
        self.assertEqual(
            settings["extendable_bookings"],
            [{"eventId": 4343, "room": "B0.27"}],
        )

        settings["extendable_bookings"] = [
            {"eventId": self.EVENT_ID},
            {"eventId": self.EVENT_ID},
        ]
        with (
            mock.patch.object(book_week, "update_settings", side_effect=update),
            self.assertRaisesRegex(SettingsError, "Multiple extension records"),
        ):
            book_week.remove_extendable_booking_by_event_id(self.EVENT_ID)


class BookingCancellationControlDomTests(unittest.TestCase):
    """Exercise the cancellation controls against representative Asimut markup."""

    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)
        cls.page = cls.browser.new_page()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_legacy_asimut_cancel_event_item_is_resolved(self):
        self.page.set_content(
            """
            <div class="cdk-overlay-pane">
              <mat-nav-list>
                <mat-list-item>
                  <mat-icon>edit</mat-icon><span>Edit event</span>
                </mat-list-item>
                <mat-list-item id="cancel-event-action">
                  <mat-icon>cancel</mat-icon><span>Cancel event</span>
                </mat-list-item>
              </mat-nav-list>
            </div>
            """
        )

        option = book_week._cancel_menu_option(self.page)

        self.assertEqual(option.get_attribute("id"), "cancel-event-action")

    def test_current_asimut_icon_plus_generic_cancel_item_is_resolved(self):
        self.page.set_content(
            """
            <div class="cdk-overlay-pane mat-mdc-dialog-panel">
              <mat-nav-list>
                <mat-list-item>
                  <mat-icon>edit</mat-icon><span>Edit</span>
                </mat-list-item>
                <mat-list-item id="cancel-action">
                  <mat-icon>cancel</mat-icon><span>Cancel</span>
                </mat-list-item>
              </mat-nav-list>
            </div>
            """
        )

        option = book_week._cancel_menu_option(self.page)

        self.assertEqual(option.get_attribute("id"), "cancel-action")

    def test_modern_accessible_cancel_reservation_item_is_resolved(self):
        self.page.set_content(
            """
            <div class="cdk-overlay-pane mat-mdc-dialog-panel">
              <div class="mat-mdc-menu-panel">
              <button mat-menu-item role="menuitem">
                <mat-icon>edit</mat-icon><span>Edit event</span>
              </button>
              <button id="cancel-reservation-action" mat-menu-item
                      role="menuitem" aria-label="Cancel reservation">
                <mat-icon>event_busy</mat-icon>
              </button>
              </div>
            </div>
            """
        )

        option = book_week._cancel_menu_option(self.page)

        self.assertEqual(option.get_attribute("id"), "cancel-reservation-action")

    def test_cancel_event_confirmation_is_explicit_and_unique(self):
        self.page.set_content(
            """
            <mat-dialog-container>
              <p>Are you sure you want to cancel this event?</p>
              <button>Go back</button>
              <button id="confirm-cancel-event">Cancel event</button>
            </mat-dialog-container>
            """
        )

        confirmation = book_week._optional_cancel_confirmation(self.page)

        self.assertEqual(
            confirmation.get_attribute("id"),
            "confirm-cancel-event",
        )


class BookingCancellationCliTests(unittest.TestCase):
    VALID = [
        "--headless",
        "--cancel-event-id",
        "4242",
        "--cancel-date",
        "2026-09-02",
        "--cancel-room",
        "B0.29",
        "--cancel-start",
        "16:00",
        "--cancel-end",
        "16:30",
    ]

    def validate(self, values):
        parser = book_week.build_argument_parser()
        args = parser.parse_args(values)
        book_week._validate_cli_args(parser, args)
        return args

    def test_complete_exact_tuple_is_accepted(self):
        args = self.validate(self.VALID)
        self.assertTrue(book_week._cancellation_requested(args))
        self.assertEqual(args.cancel_event_id, 4242)

    def test_partial_or_invalid_exact_tuple_is_rejected(self):
        invalid_values = (
            ["--cancel-event-id", "4242"],
            [*self.VALID[:-2]],
            [value if value != "4242" else "0" for value in self.VALID],
            [value if value != "16:30" else "15:30" for value in self.VALID],
            [value if value != "16:30" else "16:07" for value in self.VALID],
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(SystemExit):
                self.validate(values)

    def test_cancellation_mode_rejects_every_fallthrough_mode(self):
        for extra in (
            ["--check-only"],
            ["--plan-only"],
            ["--scheduled"],
            ["--target-time", "16:15"],
            ["--only-date", "2026-09-02"],
            ["--only-room", "B0.29"],
            ["--max-actions", "1"],
            ["--extensions-only", "--max-actions", "1", "--only-room", "B0.29", "--max-action-minutes", "30"],
            ["--setup-login"],
        ):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self.validate([*self.VALID, *extra])

    def test_run_returns_immediately_after_cancellation(self):
        args = self.validate(self.VALID)
        page = mock.Mock()
        context = mock.Mock()
        context.new_page.return_value = page
        browser = mock.Mock()
        browser.new_context.return_value = context
        playwright = mock.Mock()
        playwright.chromium.launch.return_value = browser
        manager = mock.MagicMock()
        manager.__enter__.return_value = playwright
        today = datetime.now().date()
        reservation = {
            "eventId": 4242,
            "room": "B0.29",
            "date": "2026-09-02",
            "startTime": "16:00",
            "endTime": "16:30",
        }

        with (
            mock.patch.object(book_week, "sync_playwright", return_value=manager),
            mock.patch.object(book_week, "authenticated_runtime_context_options", return_value={}),
            mock.patch.object(book_week, "restore_page_authentication"),
            mock.patch.object(book_week, "refresh_live_room_policy", return_value=mock.Mock()),
            mock.patch.object(book_week, "validate_live_scope"),
            mock.patch.object(book_week, "booking_window_dates", return_value=(today,)),
            mock.patch.object(book_week, "load_ignored_events", return_value=set()),
            mock.patch.object(book_week, "scan_agenda", return_value=(1, [reservation])),
            mock.patch.object(book_week, "reconcile_pending_mutation_receipts"),
            mock.patch.object(
                book_week,
                "cancel_reservation_exact",
                return_value=(True, 0),
            ) as cancel,
            mock.patch.object(book_week, "persist_storage_state"),
            mock.patch.object(
                book_week,
                "load_disabled_dates",
                side_effect=AssertionError("ordinary booking flow was reached"),
            ) as ordinary_flow,
        ):
            result = book_week.run_booking(
                args,
                settings={},
                practice_plan=mock.Mock(),
                room_preferences=mock.Mock(),
            )

        self.assertEqual(result, 0)
        cancel.assert_called_once()
        ordinary_flow.assert_not_called()
        context.close.assert_called_once_with()
        browser.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
